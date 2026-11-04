"""The sensitivity samples go to a file and k comes from running sums: both must match the in-memory computation."""
import random
import statistics

import nightly


class Stub:
    pass


def stub():
    n = Stub()
    n.sens_sums, n.sens_ids, n.sens_buf, n.sens_file = {}, {}, nightly.array.array("i"), None
    n.sens_flush = lambda: nightly.Nightly.sens_flush(n)
    return n


def add(n, key, x, y):
    s = n.sens_sums.get(key)
    if s is None:
        s = n.sens_sums[key] = [0, 0, 0, 0, 0]
        n.sens_ids[key] = len(n.sens_ids)
    for i, v in enumerate((1, x, y, x * y, x * x)):
        s[i] += v
    n.sens_buf.append(n.sens_ids[key])
    n.sens_buf.append(x << 16 | y & 0xffff)


def test_sums_and_medians_match_the_samples(monkeypatch):
    monkeypatch.setattr(nightly, "SENS_BUFFER", 50)       # several flushes and chunks
    rnd = random.Random(3)
    n = stub()
    samples = {}
    for p in range(40):
        key = (f"A{p}", f"B{p}")
        for _ in range(rnd.randint(1, 300)):
            x = rnd.randint(-32768, 32767) if rnd.random() < 0.05 else round(rnd.gauss(0, 300))
            y = rnd.choice((-32000, 32000)) if rnd.random() < 0.02 else round(rnd.gauss(0, 80))
            add(n, key, x, y)
            samples.setdefault(key, []).append((x, y))
            if len(n.sens_buf) >= 2 * nightly.SENS_BUFFER:
                nightly.Nightly.sens_flush(n)
    num = den = 0.0
    for key, v in samples.items():
        xs, ys = [x for x, _ in v], [y for _, y in v]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        num += sum((x - mx) * (y - my) for x, y in v)
        den += sum((x - mx) ** 2 for x in xs)
    num2 = sum((c * sxy - sx * sy) / c for c, sx, sy, sxy, sxx in n.sens_sums.values())
    den2 = sum((c * sxx - sx * sx) / c for c, sx, sy, sxy, sxx in n.sens_sums.values())
    assert abs(num2 / den2 - num / den) < 1e-9 * abs(num / den)
    k = 18.5
    kept = {key: s for key, s in n.sens_sums.items() if s[0] >= 20}
    medians = nightly.Nightly.sens_medians(n, kept, k)
    assert set(medians) == set(kept)
    for key in kept:
        want = statistics.median(y / nightly.SENS_DB_Q - k * x / nightly.SENS_LOG_Q for x, y in samples[key])
        assert medians[key] == want
