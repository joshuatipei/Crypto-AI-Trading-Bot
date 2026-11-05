"""Optional research-only logistic baseline. Never connected to execution.

Chronological split with a one-observation purge. Features use completed bars;
labels use next-bar close returns above estimated roundtrip costs. Scaling and
training see only training rows. Accuracy alone cannot authorize trading.
"""
import math
from statistics import mean, pstdev
from .data import validate


def evaluate(candles, train_fraction=.6, cost_fraction=.014, epochs=200):
    validate(candles)
    if len(candles) < 100 or not .3 <= train_fraction <= .8 or cost_fraction < 0:
        raise ValueError('Insufficient samples or invalid research parameters')
    features, labels, timestamps = [], [], []
    for i in range(20, len(candles)-1):
        history = candles[i-20:i+1]
        close = [c.close for c in history]
        features.append([close[-1]/close[-2]-1, close[-1]/mean(close)-1,
                         pstdev(close)/mean(close), history[-1].volume/(mean(c.volume for c in history) or 1)])
        labels.append(int(candles[i+1].close / candles[i].close - 1 > cost_fraction))
        timestamps.append(candles[i].timestamp)
    boundary = int(len(features)*train_fraction)
    train_x, train_y = features[:boundary], labels[:boundary]
    test_x, test_y = features[boundary+1:], labels[boundary+1:]
    centers = [mean(row[j] for row in train_x) for j in range(4)]
    scales = [pstdev(row[j] for row in train_x) or 1 for j in range(4)]
    transform = lambda row: [1] + [(row[j]-centers[j])/scales[j] for j in range(4)]
    x = [transform(row) for row in train_x]
    weights = [0.0]*5
    sigmoid = lambda value: 1/(1+math.exp(-max(-30,min(30,value))))
    for _ in range(epochs):
        gradient = [0.0]*5
        for row, label in zip(x,train_y):
            error = sigmoid(sum(a*b for a,b in zip(row,weights))) - label
            for j in range(5): gradient[j] += error*row[j]
        for j in range(5): weights[j] -= .05*(gradient[j]/len(x)+(.01*weights[j] if j else 0))
    probabilities = [sigmoid(sum(a*b for a,b in zip(transform(row),weights))) for row in test_x]
    base = max(1e-9,min(1-1e-9,mean(train_y)))
    def loss(probs):
        return -mean(y*math.log(max(p,1e-9))+(1-y)*math.log(max(1-p,1e-9)) for y,p in zip(test_y,probs))
    return {'model':'L2 logistic baseline', 'train_rows':len(train_x),'purged_rows':1,'test_rows':len(test_x),
            'train_last_timestamp':timestamps[boundary-1], 'test_first_timestamp':timestamps[boundary+1],
            'test_log_loss':loss(probabilities),'constant_baseline_log_loss':loss([base]*len(test_y)),
            'test_accuracy':mean(int(p>=.5)==y for p,y in zip(probabilities,test_y)),
            'scaling_centers':centers,'weights':weights,'cost_fraction':cost_fraction,
            'trading_enabled':False,'note':'No economic improvement established. Requires nested walk-forward net-cost trading evaluation across regimes.'}
