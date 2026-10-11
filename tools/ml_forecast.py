#!/usr/bin/env python3
"""Offline ML loop for the Blue Pill weather station (no server hosting needed).

Training (on your laptop, needs history in the channel):
    python ml_forecast.py train
    -> downloads feeds.csv (up to 8000 rows), builds features
       [T, H, dT/dt, dH/dt, recent_rain, hour], labels each row with
       "did it rain in the NEXT 30 minutes" (from field3), trains a
       Decision Tree, saves tools/rain_model.pkl.

Live forecast loop (on your laptop, every few minutes):
    python ml_forecast.py loop            # every 300 s
    python ml_forecast.py loop --once     # single shot (cron/systemd friendly)
    -> reads the latest feeds, predicts rain probability, writes it to
       field5 with the write key. The STM32 fetches
       /channels/<ID>/fields/5/last.txt every 5 min and shows it on the
       OLED forecast screen as SRC:ESP.

Channel/keys default to this project's channel; override with flags.
Requires: pip install -r tools/ml-requirements.txt
"""
import argparse
import csv
import io
import os
import pickle
import sys
import time
import urllib.request
from datetime import datetime, timezone

DEFAULT_CHANNEL = 3530859
DEFAULT_READ_KEY = "MZHJULIRGNHOSU8H"
DEFAULT_WRITE_KEY = "RFYKTNB5YD5O96SF"
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "rain_model.pkl")
FEATURES = ["T", "H", "dT", "dH", "recent_rain", "hour"]
LABEL_WINDOW_MIN = 30
HISTORY_MIN = 30


def api_get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_feeds(channel, read_key, results=8000):
    url = ("https://api.thingspeak.com/channels/%d/feeds.csv?results=%d"
           % (channel, results))
    if read_key:
        url += "&api_key=" + read_key
    rows = []
    for row in csv.DictReader(io.StringIO(api_get(url))):
        try:
            ts = datetime.strptime(row["created_at"][:19],
                                   "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            t = float(row["field1"]) if row["field1"] else None
            h = float(row["field2"]) if row["field2"] else None
            r = int(float(row["field3"])) if row["field3"] else 0
        except (ValueError, KeyError):
            continue
        if t is None or t < -40 or t > 60 or h is None or h < 0 or h > 100:
            continue  # sensor glitch rows (-99.9/-1 placeholders included)
        rows.append((ts, t, h, 1 if r else 0))
    rows.sort()
    return rows


def slope(rows, i, minutes):
    """Per-hour slope of T (idx 1) / H (idx 2) over the trailing window."""
    t0 = rows[i][0]
    j = i
    while j > 0 and (t0 - rows[j - 1][0]).total_seconds() < minutes * 60:
        j -= 1
    dt_hours = (t0 - rows[j][0]).total_seconds() / 3600.0
    if dt_hours < 1 / 60:
        return 0.0, 0.0
    return ((rows[i][1] - rows[j][1]) / dt_hours,
            (rows[i][2] - rows[j][2]) / dt_hours)


def build_dataset(rows):
    X, y = [], []
    for i, (ts, t, h, r) in enumerate(rows):
        if (ts - rows[0][0]).total_seconds() < HISTORY_MIN * 60:
            continue  # not enough history behind this row
        dT, dH = slope(rows, i, HISTORY_MIN)
        recent = 1 if any(rr for (tts, _, _, rr) in rows[:i + 1]
                          if (ts - tts).total_seconds() <= HISTORY_MIN * 60) else 0
        future = [rr for (tts, _, _, rr) in rows[i + 1:]
                  if 0 < (tts - ts).total_seconds() <= LABEL_WINDOW_MIN * 60]
        if not future:
            continue  # nothing ahead to label from
        X.append([t, h, dT, dH, recent, ts.hour + ts.minute / 60.0])
        y.append(1 if any(future) else 0)
    return X, y


def current_features(rows):
    if not rows:
        return None, "no feeds at all"
    ts, t, h, _ = rows[-1]
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > 3600:
        return None, "latest feed is %.0f min old (stale)" % (age / 60)
    if (ts - rows[0][0]).total_seconds() < HISTORY_MIN * 60:
        return None, "need 30+ min of history behind the latest feed"
    dT, dH = slope(rows, len(rows) - 1, HISTORY_MIN)
    recent = 1 if any(rr for (tts, _, _, rr) in rows
                      if (ts - tts).total_seconds() <= HISTORY_MIN * 60) else 0
    return [t, h, dT, dH, recent, ts.hour + ts.minute / 60.0], None


def do_train(args):
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.metrics import accuracy_score
    print("Downloading feeds (up to %d)..." % args.results)
    rows = fetch_feeds(args.channel, args.read_key, args.results)
    print("Usable rows: %d" % len(rows))
    X, y = build_dataset(rows)
    print("Labeled samples: %d (rain: %d)" % (len(y), sum(y)))
    if len(y) < 50 or sum(y) < 5 or sum(y) > len(y) - 5:
        print("NOT ENOUGH DATA: need 50+ samples with rain and no-rain cases.\n"
              "Let the station log for a few rainy days, then retry.", file=sys.stderr)
        return 2
    cut = int(len(y) * 0.8)  # time-ordered holdout: train on past, test on recent
    clf = DecisionTreeClassifier(max_depth=5, min_samples_leaf=10,
                                 class_weight="balanced", random_state=7)
    clf.fit(X[:cut], y[:cut])
    for name, xx, yy in (("train", X[:cut], y[:cut]), ("holdout", X[cut:], y[cut:])):
        print("%s accuracy: %.3f" % (name, accuracy_score(yy, clf.predict(xx))))
    print("Feature importances:")
    for f, imp in sorted(zip(FEATURES, clf.feature_importances_),
                         key=lambda p: -p[1]):
        print("  %-11s %.3f" % (f, imp))
    with open(args.model, "wb") as f:
        pickle.dump({"model": clf, "features": FEATURES,
                     "label_window_min": LABEL_WINDOW_MIN,
                     "trained_at": datetime.now(timezone.utc).isoformat(),
                     "channel": args.channel}, f)
    print("Saved %s" % args.model)
    return 0


def predict_once(model, rows):
    feats, err = current_features(rows)
    if err:
        return None, err
    proba = model.predict_proba([feats])[0]
    classes = list(model.classes_)
    p = float(proba[classes.index(1)] if 1 in classes else 0.0)
    return int(round(p * 100)), None


def do_loop(args):
    with open(args.model, "rb") as f:
        model = pickle.load(f)["model"]
    while True:
        try:
            rows = fetch_feeds(args.channel, args.read_key, results=200)
            pct, err = predict_once(model, rows)
            if err:
                print("[%s] skip: %s" % (datetime.now().strftime("%H:%M:%S"), err))
            else:
                url = ("https://api.thingspeak.com/update?api_key=%s&field5=%d"
                       % (args.write_key, pct))
                resp = api_get(url).strip()
                print("[%s] forecast=%d%% -> field5 (entry %s)"
                      % (datetime.now().strftime("%H:%M:%S"), pct, resp))
        except Exception as e:  # keep the loop alive on network hiccups
            print("[%s] error: %s" % (datetime.now().strftime("%H:%M:%S"), e))
        if args.once:
            return 0
        time.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", type=int, default=DEFAULT_CHANNEL)
    ap.add_argument("--read-key", default=DEFAULT_READ_KEY)
    ap.add_argument("--write-key", default=DEFAULT_WRITE_KEY)
    ap.add_argument("--model", default=MODEL_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="Train Decision Tree from channel history")
    t.add_argument("--results", type=int, default=8000)
    l = sub.add_parser("loop", help="Run live forecast loop writing field5")
    l.add_argument("--interval", type=int, default=300,
                   help="Seconds between predictions (default 300)")
    l.add_argument("--once", action="store_true", help="Single shot, then exit")
    args = ap.parse_args()
    if args.cmd == "train":
        return do_train(args)
    return do_loop(args)


if __name__ == "__main__":
    sys.exit(main())
