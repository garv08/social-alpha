import math
import random
import unittest

from social_alpha.made import MADE, Independent


class MadeTests(unittest.TestCase):
    def test_autoregressive_property(self):
        # Same check as pytorch-made's run_checks: output k may depend only on inputs earlier in the ordering.
        nin = 10
        for hidden in ([16], [12, 9]):
            for k_mask in range(3):
                m = MADE(nin, hidden, num_masks=3, seed=k_mask)
                order = m.masks[k_mask]["order"]
                rng = random.Random(1)
                base = [float(rng.random() < 0.5) for _ in range(nin)]
                z0 = m.logits(base, k_mask)
                for i in range(nin):
                    x = list(base)
                    x[i] = 1.0 - x[i]
                    z1 = m.logits(x, k_mask)
                    for k in range(nin):
                        if order[i] >= order[k]:
                            self.assertAlmostEqual(z0[k], z1[k], places=12, msg=f"output {k} depends on input {i}")

    def test_gradient_matches_finite_difference(self):
        m = MADE(6, [7], num_masks=1, seed=3)
        rng = random.Random(0)
        batch = [[float(rng.random() < 0.4) for _ in range(6)] for _ in range(5)]
        mask = m.masks[0]
        _, gW, gb = m._grads(batch, mask)

        def loss():
            return -sum(m.log_prob_mask(x, 0) for x in batch) / len(batch)

        checked = 0
        for l in range(len(m.W)):
            for j in range(len(m.W[l])):
                for i in mask["conn"][l][j][:2]:
                    old = m.W[l][j][i]
                    m.W[l][j][i] = old + 1e-6
                    up = loss()
                    m.W[l][j][i] = old - 1e-6
                    down = loss()
                    m.W[l][j][i] = old
                    self.assertAlmostEqual(gW[l][j][i], (up - down) / 2e-6, places=5)
                    checked += 1
            old = m.b[l][0]
            m.b[l][0] = old + 1e-6
            up = loss()
            m.b[l][0] = old - 1e-6
            down = loss()
            m.b[l][0] = old
            self.assertAlmostEqual(gb[l][0], (up - down) / 2e-6, places=5)
        self.assertGreater(checked, 5)

    def test_probabilities_sum_to_one(self):
        m = MADE(4, [8], num_masks=2, seed=1)
        total = 0.0
        for n in range(16):
            x = [float((n >> k) & 1) for k in range(4)]
            total += math.exp(m.log_prob(x))
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_learns_correlations_beyond_independent_bits(self):
        # bits come in perfectly correlated pairs: independent model cannot capture it, MADE can
        rng = random.Random(5)
        rows = []
        for _ in range(600):
            half = [int(rng.random() < 0.5) for _ in range(4)]
            rows.append(half + half)
        train, test = rows[:500], rows[500:]
        m = MADE(8, [24], num_masks=1, natural_ordering=True, seed=0)
        m.fit(train, epochs=40, lr=1e-2, batch_size=25, patience=8)
        self.assertLess(m.nll(test), Independent(train).nll(test) - 1.5)

    def test_roundtrip_json(self):
        m = MADE(5, [6], num_masks=2, seed=2)
        x = [1.0, 0.0, 1.0, 1.0, 0.0]
        self.assertAlmostEqual(MADE.from_json(m.to_json()).log_prob(x), m.log_prob(x), places=12)


if __name__ == "__main__":
    unittest.main()
