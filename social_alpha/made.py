"""MADE: Masked Autoencoder for Distribution Estimation (Germain et al. 2015), in pure Python.

A port of Andrej Karpathy's pytorch-made (https://github.com/karpathy/pytorch-made, MIT) with
the same mask construction, ensemble of orderings (num_masks) and Bernoulli outputs, written
without NumPy/PyTorch so it runs on CPUs that cannot load them (TOP targets pre-SSE4.2 x86).

Use here: each row is a binary vector of which TOP strategies are long on a stock at a date.
MADE models p(x) = prod_k p(x_k | x_<k) for some ordering, so log p(x) tells how typical a
strategy-state combination is. Training one model on social calls and one on random
stock-days gives a log-likelihood ratio: "how much does this stock today look like what
people post about".
"""

from __future__ import annotations

import json
import math
import random


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _softplus(z):
    return z + math.log1p(math.exp(-z)) if z > 0 else math.log1p(math.exp(z))


class MADE:
    def __init__(self, nin: int, hidden_sizes=(48,), num_masks: int = 1, natural_ordering: bool = False, seed: int = 0):
        self.nin = nin
        self.hidden = list(hidden_sizes)
        self.num_masks = max(1, int(num_masks))
        self.natural_ordering = natural_ordering
        self.seed = seed
        sizes = [nin] + self.hidden + [nin]
        rng = random.Random(seed)
        self.W, self.b = [], []
        for h0, h1 in zip(sizes, sizes[1:]):
            bound = 1.0 / math.sqrt(h0)  # torch nn.Linear default init
            self.W.append([[rng.uniform(-bound, bound) for _ in range(h0)] for _ in range(h1)])
            self.b.append([rng.uniform(-bound, bound) for _ in range(h1)])
        self.masks = [self._build_mask(k) for k in range(self.num_masks)]
        self._adam = None

    # ---- masks (same algorithm as pytorch-made update_masks) ----------------
    def _build_mask(self, k):
        rng = random.Random(1000 + self.seed * 7919 + k)
        L = len(self.hidden)
        m = {-1: list(range(self.nin)) if self.natural_ordering else rng.sample(range(self.nin), self.nin)}
        for l in range(L):
            lo = min(m[l - 1])
            m[l] = [rng.randint(lo, self.nin - 2) if self.nin > 1 else 0 for _ in range(self.hidden[l])]
        conn = []
        for l in range(L):  # hidden layers: m_prev <= m_this
            conn.append([[i for i, mi in enumerate(m[l - 1]) if mi <= mj] for mj in m[l]])
        conn.append([[i for i, mi in enumerate(m[L - 1]) if mi < mk] for mk in m[-1]])  # output: strict
        return {"order": m[-1], "conn": conn}

    # ---- forward / backward ---------------------------------------------------
    def _forward(self, x, mask):
        acts, pres = [x], []
        a = x
        last = len(self.W) - 1
        for l, (W, b, conn) in enumerate(zip(self.W, self.b, mask["conn"])):
            z = [bj + sum(Wj[i] * a[i] for i in cj) for Wj, bj, cj in zip(W, b, conn)]
            pres.append(z)
            a = z if l == last else [v if v > 0 else 0.0 for v in z]
            acts.append(a)
        return acts, pres

    def logits(self, x, mask_index=0):
        return self._forward(x, self.masks[mask_index])[1][-1]

    def log_prob_mask(self, x, mask_index=0) -> float:
        z = self.logits(x, mask_index)
        # log sigmoid(z) if x=1 else log(1-sigmoid(z)) = -softplus(-z) / -softplus(z)
        return -sum(_softplus(-zk) if xk else _softplus(zk) for zk, xk in zip(z, x))

    def log_prob(self, x) -> float:
        """Ensemble over orderings: log mean_m p_m(x)."""
        lps = [self.log_prob_mask(x, k) for k in range(self.num_masks)]
        top = max(lps)
        return top + math.log(sum(math.exp(v - top) for v in lps) / len(lps))

    def _grads(self, batch, mask):
        gW = [[[0.0] * len(row) for row in W] for W in self.W]
        gb = [[0.0] * len(b) for b in self.b]
        loss = 0.0
        L = len(self.W)
        for x in batch:
            acts, pres = self._forward(x, mask)
            z = pres[-1]
            delta = []
            for zk, xk in zip(z, x):
                p = _sigmoid(zk)
                delta.append(p - xk)
                loss += _softplus(-zk) if xk else _softplus(zk)
            for l in range(L - 1, -1, -1):
                a_prev = acts[l]
                W, conn, gWl, gbl = self.W[l], mask["conn"][l], gW[l], gb[l]
                g_prev = [0.0] * len(a_prev) if l > 0 else None
                for j, dj in enumerate(delta):
                    if dj == 0.0:
                        continue
                    gbl[j] += dj
                    row, grow = W[j], gWl[j]
                    for i in conn[j]:
                        ai = a_prev[i]
                        if ai:
                            grow[i] += dj * ai
                        if g_prev is not None:
                            g_prev[i] += dj * row[i]
                if l > 0:
                    zprev = pres[l - 1]
                    delta = [g if zp > 0 else 0.0 for g, zp in zip(g_prev, zprev)]
        n = len(batch)
        for gWl in gW:
            for row in gWl:
                for i in range(len(row)):
                    row[i] /= n
        for gbl in gb:
            for j in range(len(gbl)):
                gbl[j] /= n
        return loss / n, gW, gb

    def _adam_step(self, gW, gb, lr, wd, t, b1=0.9, b2=0.999, eps=1e-8):
        if self._adam is None:
            self._adam = {"mW": [[[0.0] * len(r) for r in W] for W in self.W], "vW": [[[0.0] * len(r) for r in W] for W in self.W],
                          "mb": [[0.0] * len(b) for b in self.b], "vb": [[0.0] * len(b) for b in self.b]}
        s = self._adam
        c1, c2 = 1 - b1 ** t, 1 - b2 ** t
        for W, G, M, V in zip(self.W, gW, s["mW"], s["vW"]):
            for row, grow, mrow, vrow in zip(W, G, M, V):
                for i in range(len(row)):
                    g = grow[i] + wd * row[i]
                    if g == 0.0 and mrow[i] == 0.0:
                        continue
                    mrow[i] = b1 * mrow[i] + (1 - b1) * g
                    vrow[i] = b2 * vrow[i] + (1 - b2) * g * g
                    row[i] -= lr * (mrow[i] / c1) / (math.sqrt(vrow[i] / c2) + eps)
        for b, G, M, V in zip(self.b, gb, s["mb"], s["vb"]):
            for j in range(len(b)):
                g = G[j]
                M[j] = b1 * M[j] + (1 - b1) * g
                V[j] = b2 * V[j] + (1 - b2) * g * g
                b[j] -= lr * (M[j] / c1) / (math.sqrt(V[j] / c2) + eps)

    @staticmethod
    def split(data, val_frac=0.15, seed=0):
        rows = [list(map(float, x)) for x in data]
        random.Random(seed).shuffle(rows)
        n_val = int(len(rows) * val_frac) if len(rows) >= 40 else 0
        return rows[n_val:], rows[:n_val]

    def init_output_bias(self, rows, alpha=1.0):
        """Start each output at its marginal log-odds, so training begins at the independent-bits model."""
        n = len(rows)
        for k in range(self.nin):
            p = (sum(r[k] for r in rows) + alpha) / (n + 2 * alpha)
            self.b[-1][k] = math.log(p / (1 - p))

    def fit(self, data, epochs=30, batch_size=32, lr=3e-3, weight_decay=1e-4, val_frac=0.15, patience=4,
            seed=0, log=None, val=None) -> dict:
        """Adam on mean NLL, cycling the mask each minibatch (as pytorch-made). Early stop on validation NLL.

        data is split into train/validation unless val is given.
        """
        rng = random.Random(seed)
        if val is None:
            train, val = self.split(data, val_frac, seed)
        else:
            train, val = [list(map(float, x)) for x in data], [list(map(float, x)) for x in val]
        if self._adam is None and train:
            self.init_output_bias(train)
        best, best_state, bad, t, hist = float("inf"), None, 0, 0, []
        for ep in range(epochs):
            rng.shuffle(train)
            tot = 0.0
            for s in range(0, len(train), batch_size):
                t += 1
                mask = self.masks[t % self.num_masks]
                loss, gW, gb = self._grads(train[s:s + batch_size], mask)
                self._adam_step(gW, gb, lr, weight_decay, t)
                tot += loss * len(train[s:s + batch_size])
            tr = tot / max(len(train), 1)
            vl = self.nll(val) if val else tr
            hist.append({"epoch": ep + 1, "train_nll": round(tr, 4), "val_nll": round(vl, 4)})
            if log:
                log(f"    epoch {ep + 1}: train {tr:.3f}  val {vl:.3f} nats")
            if vl < best - 1e-4:
                best, bad, best_state = vl, 0, json.dumps({"W": self.W, "b": self.b})
            else:
                bad += 1
                if bad >= patience:
                    break
        if best_state:
            st = json.loads(best_state)
            self.W, self.b = st["W"], st["b"]
        return {"history": hist, "best_val_nll": round(best, 4), "n_train": len(train), "n_val": len(val)}

    def nll(self, rows) -> float:
        rows = list(rows)
        return -sum(self.log_prob(list(map(float, x))) for x in rows) / max(len(rows), 1)

    # ---- persistence ---------------------------------------------------------
    def to_json(self) -> dict:
        return {"nin": self.nin, "hidden": self.hidden, "num_masks": self.num_masks,
                "natural_ordering": self.natural_ordering, "seed": self.seed, "W": self.W, "b": self.b}

    @classmethod
    def from_json(cls, d: dict) -> "MADE":
        m = cls(d["nin"], d["hidden"], d["num_masks"], d["natural_ordering"], d["seed"])
        m.W, m.b = d["W"], d["b"]
        return m


class Independent:
    """Baseline: independent Bernoulli per bit (what MADE must beat to show it learned structure)."""

    def __init__(self, rows, alpha=1.0):
        rows = [list(map(float, x)) for x in rows]
        n = len(rows)
        self.p = [(sum(r[k] for r in rows) + alpha) / (n + 2 * alpha) for k in range(len(rows[0]))] if rows else []

    def log_prob(self, x) -> float:
        return sum(math.log(p if xk else 1 - p) for p, xk in zip(self.p, x))

    def nll(self, rows) -> float:
        rows = list(rows)
        return -sum(self.log_prob(list(map(float, x))) for x in rows) / max(len(rows), 1)
