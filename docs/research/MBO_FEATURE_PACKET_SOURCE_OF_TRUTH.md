---
title: MBO Feature Packet Source of Truth
intention: Canonical research specification for order-level MBO feature packets.
status: source_of_truth
source: user-pasted research note, 2026-06-05
---

# MBO Feature Packet Source of Truth

This document is the source of truth for `mbo_feature_packet_v1`: MBO is order-level event physics, not merely deeper MBP.

Yes. The way to unlock MBO is to stop treating it as “more depth” and start treating it as **order-level event physics**.

MBO gives you the individual order lifecycle: add, cancel, modify, trade/fill, order ID, side, price, size, timestamps, and sequencing. Databento describes MBO as every order-book event across every price level keyed by order ID, and CME describes it as order-level data with queue position, full depth, and individual order size. MBP-10 is only aggregated top-10 depth by price level, so MBO can recreate MBP-style features, but MBP cannot recreate order-level queue behavior. ([Databento][1])

Below is the mathematical stack I would use.

---

# 1. Core MBO State Representation

At event time (t), represent the live book as:

[
B_t = {o_j = (id_j, side_j, price_j, qty_j, priority_j, t^{birth}_j, age_j)}
]

For each bid/ask level:

[
Q^b_i(t)=\sum_{o\in bid_i} qty_o
]

[
Q^a_i(t)=\sum_{o\in ask_i} qty_o
]

[
N^b_i(t)=#{o\in bid_i}, \quad N^a_i(t)=#{o\in ask_i}
]

Basic market state:

[
mid_t = \frac{a_1(t)+b_1(t)}{2}
]

[
spread_t = a_1(t)-b_1(t)
]

[
tick_spread_t = \frac{spread_t}{tick_size}
]

This state engine matters more than the model. If the reconstructed book is wrong, every alpha feature after it is contaminated.

---

# 2. Standard Depth Imbalance

This is the baseline.

[
I_K(t)=
\frac{
\sum_{i=1}^{K} w_i Q^b_i(t) - \sum_{i=1}^{K} w_i Q^a_i(t)
}{
\sum_{i=1}^{K} w_i Q^b_i(t) + \sum_{i=1}^{K} w_i Q^a_i(t)
}
]

Where:

[
w_i = e^{-\lambda(i-1)}
]

or:

[
w_i = \frac{1}{i}
]

Interpretation:

* (I_K > 0): more bid-side support
* (I_K < 0): more ask-side pressure
* (I_K \approx 0): balanced book

But this alone is ordinary. Every serious shop calculates some version of it.

---

# 3. Order-Count Imbalance

MBO lets you separate **volume imbalance** from **participant fragmentation**.

[
OCI_K(t)=
\frac{
\sum_{i=1}^{K} w_i N^b_i(t) - \sum_{i=1}^{K} w_i N^a_i(t)
}{
\sum_{i=1}^{K} w_i N^b_i(t) + \sum_{i=1}^{K} w_i N^a_i(t)
}
]

Why it matters:

| Situation                            | Volume Imbalance | Order-Count Imbalance | Interpretation                        |
| ------------------------------------ | ---------------: | --------------------: | ------------------------------------- |
| One huge bid order                   |             High |                   Low | Possible iceberg/block/liquidity wall |
| Many small bid orders                |             High |                  High | Broad liquidity support               |
| High size, low count, short lifespan |             High |                   Low | Possibly fragile/fleeting liquidity   |
| Low size, high count                 |              Low |                  High | Fragmented shallow queue              |

This is one of the first MBO-only unlocks.

---

# 4. Microprice

The microprice estimates where the fair short-term price leans based on top-of-book pressure.

[
MP_1(t)=
\frac{
a_1(t)Q^b_1(t)+b_1(t)Q^a_1(t)
}{
Q^b_1(t)+Q^a_1(t)
}
]

Signal:

[
micro_drift_t = MP_1(t)-mid_t
]

If the bid queue is much larger than the ask queue, the microprice moves closer to the ask, implying upward pressure.

DeepLOB explicitly uses book structure, price/volume interactions, and imbalance-like representations to predict short-horizon price movement from LOB data, which supports using these book-shape features as first-class inputs rather than treating the book as a flat table. 

---

# 5. Multi-Level Microprice

Extend microprice beyond level 1:

[
MP_K(t)=
\frac{
\sum_{i=1}^{K} w_i \left[a_i(t)Q^b_i(t)+b_i(t)Q^a_i(t)\right]
}{
\sum_{i=1}^{K} w_i \left[Q^b_i(t)+Q^a_i(t)\right]
}
]

Then:

[
multi_micro_drift_t = MP_K(t)-mid_t
]

Useful when level 1 is noisy or spoof-like, but levels 2–10 show cleaner pressure.

---

# 6. Order Flow Imbalance — OFI

OFI is more powerful than static imbalance because it measures **change**, not just shape.

For each event:

[
\Delta Q^b_i =
\begin{cases}
+q, & \text{bid add} \
-q, & \text{bid cancel/fill} \
0, & \text{otherwise}
\end{cases}
]

[
\Delta Q^a_i =
\begin{cases}
+q, & \text{ask add} \
-q, & \text{ask cancel/fill} \
0, & \text{otherwise}
\end{cases}
]

Then:

[
OFI_K(t,\Delta t)=
\sum_{i=1}^{K} w_i
\left[
\Delta Q^b_i(t,\Delta t)-\Delta Q^a_i(t,\Delta t)
\right]
]

Normalized:

[
NOFI_K=
\frac{OFI_K}{\sum_{i=1}^{K} w_i(Q^b_i+Q^a_i)+\epsilon}
]

Cont, Kukanov, and Stoikov found that short-horizon price changes are strongly connected to order flow imbalance, with a roughly linear relation and a slope inversely related to market depth. ([arXiv][2])

---

# 7. Multi-Level OFI Vector

Instead of collapsing all levels:

[
\mathbf{MLOFI}_t =
\left[
OFI_1, OFI_2, \ldots, OFI_K
\right]
]

This lets the model distinguish:

* pressure at touch
* pressure behind touch
* liquidity retreat
* liquidity stacking
* sweep preparation
* passive absorption

Example interpretation:

| Pattern                       | Meaning                              |
| ----------------------------- | ------------------------------------ |
| (OFI_1 > 0), deeper flat      | Immediate bid support                |
| (OFI_1 < 0), (OFI_{5-10} > 0) | Touch is weak, deeper support exists |
| (OFI_1 > 0), (OFI_{5-10} < 0) | Possible fake top-level strength     |
| All bid levels canceling      | Liquidity vacuum risk                |

---

# 8. Imbalance Velocity and Acceleration

Static imbalance is weak. Directional pressure comes from how imbalance changes.

[
v_I(t)=\frac{I_K(t)-I_K(t-\Delta t)}{\Delta t}
]

[
a_I(t)=\frac{v_I(t)-v_I(t-\Delta t)}{\Delta t}
]

Use event-time windows:

* last 10 events
* last 50 events
* last 100 events
* last 10 ms
* last 100 ms
* last 1 second

Signal example:

[
pressure_burst = z(v_I) + z(a_I)
]

The point is to detect the book **turning**, not just being imbalanced.

---

# 9. Queue Position Mathematics

This is one of the main reasons to use MBO.

For an order (o) at price level (p):

[
Ahead_o(t)=\sum_{j: priority_j < priority_o, price_j=p} qty_j
]

[
Behind_o(t)=\sum_{j: priority_j > priority_o, price_j=p} qty_j
]

Queue rank:

[
rank_o(t)=
\frac{Ahead_o(t)}
{Ahead_o(t)+qty_o+Behind_o(t)}
]

Front-of-queue liquidity:

[
FQ_i(t)=
\sum_{o \in level_i, rank_o < r} qty_o
]

Back-of-queue liquidity:

[
BQ_i(t)=
\sum_{o \in level_i, rank_o \ge r} qty_o
]

Queue-front imbalance:

[
QFI_i=
\frac{FQ^b_i-FQ^a_i}{FQ^b_i+FQ^a_i+\epsilon}
]

This is deeper than normal imbalance because **front-of-queue volume matters more than back-of-queue volume**.

---

# 10. Expected Fill Probability

For a passive order sitting at queue position (Ahead_o(t)):

[
P(fill \le T)
=============

1-
\exp\left(
-\int_t^T \lambda_{deplete}(u, Ahead_o(u))du
\right)
]

Where:

[
\lambda_{deplete}
=================

\lambda_{marketable}
+
\lambda_{cancel_ahead}
]

Practical approximation:

[
ETF_o =
\frac{Ahead_o + qty_o}
{\widehat{MO}_{opposite} + \widehat{CancelAhead}}
]

Where (ETF) means expected time to fill.

Use this for:

* whether to post
* whether to cancel
* whether queue position is worth holding
* whether a fill is likely before adverse selection arrives

---

# 11. Queue Depletion Probability

For the best bid:

[
P(bid_depletes)=
P(Q^b_1(t+h)=0)
]

Estimate from event intensities:

[
hazard^b_{deplete}
==================

\lambda^b_{cancel}
+
\lambda^{sell}_{market}
-----------------------

\lambda^b_{add}
]

Then:

[
P(bid_depletes \le h)
=====================

1-e^{-hazard^b_{deplete}h}
]

Similarly for ask:

[
hazard^a_{deplete}
==================

\lambda^a_{cancel}
+
\lambda^{buy}_{market}
----------------------

\lambda^a_{add}
]

Directional signal:

[
DepletionEdge =
P(ask_depletes)-P(bid_depletes)
]

If ask depletion probability is higher, upside move risk is higher.

---

# 12. Order Age / Survival Analysis

Every resting order has a lifespan.

[
age_o(t)=t-t^{birth}_o
]

Survival function:

[
S(\tau)=P(T_o>\tau)
]

Hazard rate:

[
h(\tau)=\frac{f(\tau)}{S(\tau)}
]

MBO lets you distinguish:

| Order Type         | Typical Signal                 |
| ------------------ | ------------------------------ |
| Old resting order  | More credible liquidity        |
| New large order    | Could be real or bait          |
| Fast cancel order  | Fleeting liquidity             |
| Repeated refresh   | Possible iceberg/replenishment |
| Modify-heavy order | Active queue management        |

Age-weighted liquidity:

[
AWQ^b_i=
\sum_{o\in bid_i} qty_o \cdot \log(1+age_o)
]

[
AWQ^a_i=
\sum_{o\in ask_i} qty_o \cdot \log(1+age_o)
]

Age-weighted imbalance:

[
AWI_K=
\frac{
\sum_i w_i AWQ^b_i-\sum_i w_i AWQ^a_i
}{
\sum_i w_i AWQ^b_i+\sum_i w_i AWQ^a_i+\epsilon
}
]

This helps separate durable liquidity from bait.

---

# 13. Cancellation Pressure

Cancellations are not noise. They are often the first sign that liquidity providers are stepping away.

[
CancelRate^b_i=
\frac{\sum cancel_qty^b_i}{\Delta t}
]

[
CancelRate^a_i=
\frac{\sum cancel_qty^a_i}{\Delta t}
]

Cancellation imbalance:

[
CI_K=
\frac{
\sum_i w_i CancelRate^a_i-\sum_i w_i CancelRate^b_i
}{
\sum_i w_i CancelRate^a_i+\sum_i w_i CancelRate^b_i+\epsilon
}
]

Interpretation:

* ask cancellations rising → upside vulnerability
* bid cancellations rising → downside vulnerability

Important: cancellation imbalance is often earlier than trade imbalance.

---

# 14. Replenishment / Absorption Score

Absorption occurs when aggressive flow hits one side, but price does not move because passive liquidity replenishes.

For bid absorption:

[
AbsorbBid =
\frac{
SellAggressorVolume
}{
|\Delta mid|+\epsilon
}
\cdot
ReplenishmentBid
]

Where:

[
ReplenishmentBid =
\frac{
AddQty^b_{same\ level,\ after\ trade}
}{
SellAggressorVolume+\epsilon
}
]

For ask absorption:

[
AbsorbAsk =
\frac{
BuyAggressorVolume
}{
|\Delta mid|+\epsilon
}
\cdot
ReplenishmentAsk
]

Net absorption:

[
NetAbsorb =
AbsorbBid - AbsorbAsk
]

Interpretation:

| Signal                        | Meaning                                    |
| ----------------------------- | ------------------------------------------ |
| High bid absorption           | Sellers hitting bid but price not breaking |
| High ask absorption           | Buyers lifting ask but price not breaking  |
| Absorption + replenishment    | Possible large passive participant         |
| Aggression without absorption | Price more likely to move                  |

---

# 15. Iceberg / Hidden Liquidity Detection

CME notes that native iceberg displayed quantity refreshes can retain the same OrderID, while synthetic iceberg behavior may appear differently depending on implementation. That does not mean every refresh is an iceberg, but it gives you an observable feature input for a hidden-liquidity score. ([CME Group][3])

Possible score:

[
IcebergScore_p =
z(ExecutedQty_p - DisplayedQty_p)
+
z(RefreshCount_p)
+
z(SameIDReplenishment_p)
+
z(ReaddSpeed_p)
]

Where:

[
ReaddSpeed_p=
\frac{AddQty_{p,\ after\ fill}}{\Delta t+\epsilon}
]

A practical hidden-liquidity detector should look for:

* repeated fills at same price
* minimal price movement
* displayed size refreshing
* same order ID persistence where venue rules support it
* fast re-adds after executions
* executed volume exceeding visible displayed depth

---

# 16. Fleeting Liquidity / Spoof-Like Risk

Do not build this as “how to spoof.” Build it as a **do-not-trust-this-liquidity** detector.

Ephemeral liquidity ratio:

[
ELR_i =
\frac{
\sum qty_{added\ then\ canceled\ within\ \tau,\ no\ fill}
}{
\sum AddQty_i+\epsilon
}
]

Layering pressure:

[
LayerPressure =
\sum_i
w_i
\cdot
qty_i
\cdot
P(cancel\ quickly|state)
\cdot
distanceWeight_i
]

Distance weight:

[
distanceWeight_i = e^{-\lambda(i-1)}
]

Fleeting-liquidity imbalance:

[
FLI_K=
\frac{
\sum_i w_i ELR^a_i Q^a_i -
\sum_i w_i ELR^b_i Q^b_i
}{
\sum_i w_i ELR^a_i Q^a_i+
\sum_i w_i ELR^b_i Q^b_i+\epsilon
}
]

Interpretation:

* high ask-side fleeting liquidity → visible ask wall may be fake
* high bid-side fleeting liquidity → visible bid wall may be fake

---

# 17. Liquidity Entropy

MBO lets you measure whether a level is made of many orders or a few dominant orders.

At level (i):

[
p_o=\frac{qty_o}{Q_i}
]

[
H_i=-\sum_{o\in level_i} p_o\log(p_o)
]

Normalized:

[
H^{norm}_i=\frac{H_i}{\log(N_i+\epsilon)}
]

Interpretation:

| Entropy                 | Meaning                                       |
| ----------------------- | --------------------------------------------- |
| Low entropy             | One/few large orders dominate                 |
| High entropy            | Many smaller orders                           |
| Low entropy + short age | Fragile wall                                  |
| Low entropy + long age  | Potential real institutional resting interest |
| High entropy + old age  | Broad stable liquidity                        |

Entropy is a strong MBO-only feature.

---

# 18. Liquidity Concentration / Wall Quality

Raw wall size is weak. Wall quality is better.

[
WallScore_i =
\frac{Q_i}{\sum_{j=1}^{K} Q_j+\epsilon}
]

But quality-adjusted wall score:

[
QWall_i =
WallScore_i
\cdot
AgeScore_i
\cdot
(1-ELR_i)
\cdot
FillResistance_i
]

Where:

[
AgeScore_i = z(AWQ_i)
]

[
FillResistance_i =
\frac{AggressorVolumeAtLevel_i}
{|\Delta price|+\epsilon}
]

This separates:

* real wall
* fake wall
* exhausted wall
* absorbing wall
* bait wall

---

# 19. Book Shape: Slope, Curvature, Convexity

Depth curve:

[
D^b(k)=\sum_{i=1}^{k} Q^b_i
]

[
D^a(k)=\sum_{i=1}^{k} Q^a_i
]

Slope:

[
Slope^b =
\frac{D^b(K)-D^b(1)}{K-1}
]

[
Slope^a =
\frac{D^a(K)-D^a(1)}{K-1}
]

Curvature:

[
Curv^b_i =
Q^b_{i+1}-2Q^b_i+Q^b_{i-1}
]

Shape imbalance:

[
ShapeImb =
\frac{Slope^b-Slope^a}{|Slope^b|+|Slope^a|+\epsilon}
]

Useful states:

| Shape                         | Meaning                                 |
| ----------------------------- | --------------------------------------- |
| Thin near touch, thick deeper | Price can move then stall               |
| Thick near touch, thin deeper | Break can accelerate after wall failure |
| Convex support                | Layered defense                         |
| Concave support               | Weak behind the touch                   |

---

# 20. Liquidity Vacuum Score

A liquidity vacuum is when one side of the book disappears faster than price has moved.

[
Vacuum^b =
z(CancelRate^b)
+
z(-AddRate^b)
+
z(-Q^b_{1:K})
+
z(SpreadExpansion)
]

[
Vacuum^a =
z(CancelRate^a)
+
z(-AddRate^a)
+
z(-Q^a_{1:K})
+
z(SpreadExpansion)
]

Directional vacuum:

[
VacuumEdge =
Vacuum^a - Vacuum^b
]

If ask liquidity disappears, upside path opens. If bid liquidity disappears, downside path opens.

---

# 21. Hawkes Process Event Modeling

MBO is naturally modeled as a marked point process.

Define event types:

[
c \in {AddBid, AddAsk, CancelBid, CancelAsk, TradeBuy, TradeSell, ModifyBid, ModifyAsk}
]

Multivariate Hawkes intensity:

[
\lambda_c(t)=
\mu_c(X_t)
+
\sum_{j:t_j<t}
\alpha_{c,c_j}e^{-\beta_{c,c_j}(t-t_j)}
]

Where:

* (\mu_c(X_t)): baseline intensity conditional on book state
* (\alpha_{c,c_j}): how much event (j) excites event type (c)
* (\beta): decay speed
* (X_t): current book state

Use cases:

* predict next event type
* estimate cancellation waves
* detect self-exciting liquidity shocks
* measure market reflexivity
* model sweep cascades

Hawkes processes are widely used in high-frequency finance because they model self-excitation and cross-excitation across event streams, including full-order-book dynamics. ([arXiv][4])

---

# 22. Queue-Reactive Model

This is one of the most important institutional models for MBO.

State:

[
X_t=
(Q^b_1,\ldots,Q^b_K,Q^a_1,\ldots,Q^a_K,spread,I_K)
]

Each event has state-dependent intensity:

[
\lambda_c(t)=\lambda_c(X_t)
]

The book is treated as a Markov queuing system where order-flow intensities depend on the current state of the book. That is the core idea of the queue-reactive model. ([arXiv][5])

Next-move probability can be modeled as:

[
P(up|X_t)
=========

P(ask\ depleted\ before\ bid\ depleted|X_t)
]

Generator equation:

[
\mathcal{L}p(X)=0
]

Boundary conditions:

[
p(X)=1 \quad \text{if ask depleted}
]

[
p(X)=0 \quad \text{if bid depleted}
]

This is cleaner than throwing raw MBO into a neural net because it gives you an interpretable probability of the next price move.

---

# 23. Adverse Selection Score

For a passive order, estimate:

[
EV_{post} =
P(fill)
\cdot
SpreadCapture
-------------

## P(fill)\cdot P(adverse|fill)\cdot AdverseMove

Fees
]

A practical bid-side version:

[
EV_{bid} =
P(fill)
\cdot
(mid_{t+h}-bid_price)
---------------------

P(fill)\cdot P(down\ move|fill)
\cdot
Loss_h
------

fees
]

Cancel if:

[
EV_{hold} < EV_{cancel}
]

Adverse-selection probability:

[
P(adverse|fill)=
\sigma
\left(
\beta_0
+
\beta_1 NOFI
+
\beta_2 DepletionEdge
+
\beta_3 CancelPressure
+
\beta_4 ToxicAggression
+
\beta_5 VacuumScore
\right)
]

This turns MBO into a trade manager input, not just an alpha input.

---

# 24. Avellaneda–Stoikov Market-Making Layer

For quote placement, use inventory-adjusted reservation price:

[
r_t = mid_t - q_t\gamma\sigma^2(T-t)
]

Where:

* (q_t): current inventory
* (\gamma): risk aversion
* (\sigma): short-horizon volatility
* (T-t): remaining horizon

Order-arrival intensity as a function of quote distance:

[
\lambda(\delta)=Ae^{-k\delta}
]

MBO improves this because (A) and (k) can be estimated conditionally on:

* queue depth
* queue age
* cancellation rate
* OFI
* spread
* volatility
* event regime

Avellaneda and Stoikov model the dealer’s limit-order problem around inventory risk and Poisson arrivals of market buy/sell orders, then calibrate bid/ask quotes around the limit order book. ([Cornell People][6])

---

# 25. Latency-Adjusted Edge

Since you are colocated but CPU-based, your features need a latency haircut.

Event intensity:

[
\lambda_{any}=
\frac{N_{events}}{\Delta t}
]

Probability the book changes before your order arrives:

[
P(stale)=1-e^{-\lambda_{any}\cdot latency}
]

Latency-adjusted expected value:

[
EV_{latency_adj}
================

## EV_{raw}

P(stale)\cdot ExpectedAdverseMove
]

If:

[
EV_{latency_adj} \le 0
]

do not send the order.

This is important because MBO can create false confidence. Seeing more detail does not help if the detail decays before your order reaches the venue.

---

# 26. Information-Theoretic Features

## Entropy of the book

[
H(B_t)=-\sum_i p_i\log(p_i)
]

Where (p_i) is the share of liquidity at level (i).

## KL divergence from normal book shape

[
D_{KL}(P_t || P_{ref})
======================

\sum_i P_t(i)\log\frac{P_t(i)}{P_{ref}(i)}
]

Use this to detect abnormal book states.

## Event surprise

[
Surprise(e_t)=-\log P(e_t|X_{t-1})
]

A large surprise means the event was unlikely under current regime.

## Mutual information

[
I(F;Y)=
\sum_{f,y}p(f,y)\log\frac{p(f,y)}{p(f)p(y)}
]

Use this to rank features by actual information about future movement.

---

# 27. Cross-Asset / Cross-Venue MBO Pressure

For your equities, futures, and options setup, the real edge may come from **cross-market pressure**, not just one book.

Cross-impact model:

[
\Delta mid_i(t+h)=
\sum_j \beta_{ij} OFI_j(t)
+
\epsilon_i
]

Where:

* (i): target instrument
* (j): related instruments
* (OFI_j): order-flow imbalance from related book

Examples:

| Target              | Leading Inputs                               |
| ------------------- | -------------------------------------------- |
| SPY                 | ES, QQQ, major components                    |
| QQQ                 | NQ, NVDA, AAPL, MSFT                         |
| 0DTE options        | underlying equity/future MBO + options flow  |
| single-name runners | stock MBO + sector ETF + market index future |

Lead-lag score:

[
LeadLag_{j\to i}
================

corr(OFI_j(t),\Delta mid_i(t+h))
]

Time-varying version:

[
\beta_t =
\beta_{t-1}
+
\eta_t
]

via Kalman filtering.

---

# 28. Options / 0DTE Gamma-Weighted Pressure

For options-linked microstructure:

[
DeltaPressure =
\sum_k OFI_k^{option}\cdot \Delta_k
]

[
GammaPressure =
\sum_k OFI_k^{option}\cdot \Gamma_k
]

[
CharmPressure =
\sum_k OFI_k^{option}\cdot Charm_k
]

Then connect to underlying:

[
UnderlyingPressure =
OFI_{stock}
+
\alpha DeltaPressure
+
\beta GammaPressure
+
\chi CharmPressure
]

For 0DTE, gamma pressure can dominate because hedging flows can become nonlinear near strikes.

---

# 29. Signature Features / Rough Path Features

This is more inventive and worth testing.

Represent the event stream as a path:

[
X_t =
(OFI_t, CancelImb_t, TradeImb_t, Spread_t, MicroDrift_t)
]

Path signature:

[
S(X)=
\left(
1,
\int dX^i,
\int dX^i dX^j,
\int dX^i dX^j dX^k,
\ldots
\right)
]

Why useful:

* captures event ordering
* captures path dependency
* avoids manually designing every interaction
* works well on irregular event-time data

Example:

A then B may mean something different from B then A.

Normal feature models often lose that.

---

# 30. Optimal Transport / Wasserstein Book Distance

Compare the current book shape to historical regimes.

Define normalized bid/ask liquidity distributions:

[
P_t(i)=\frac{Q_i(t)}{\sum_j Q_j(t)}
]

Distance to reference regime:

[
W_p(P_t,P_{ref})
================

\left(
\inf_{\gamma\in\Gamma(P_t,P_{ref})}
\sum_{i,j} d(i,j)^p \gamma(i,j)
\right)^{1/p}
]

Use cases:

* current book resembles breakout regime
* current book resembles absorption regime
* current book resembles fake-liquidity regime
* current book resembles sweep regime

This is stronger than simple z-scores because it compares the **shape** of liquidity.

---

# 31. Graph Theory on Order Lifecycles

Treat every order ID as a node with lifecycle transitions.

Example transitions:

[
Add \rightarrow Modify \rightarrow Cancel
]

[
Add \rightarrow PartialFill \rightarrow Refresh
]

[
Add \rightarrow Fill
]

Build transition matrix:

[
P_{ij}=P(state_{t+1}=j|state_t=i)
]

Order-type motif frequency:

[
MotifScore_m =
\frac{Count(motif_m)}{TotalMotifs}
]

Useful motifs:

| Motif                              | Interpretation                |
| ---------------------------------- | ----------------------------- |
| add-cancel-fast                    | fleeting liquidity            |
| add-fill-refresh                   | hidden/replenishing liquidity |
| add-modify-cancel                  | active queue management       |
| repeated same-price refresh        | absorption/iceberg candidate  |
| large add behind touch then cancel | possible pressure bluff       |

---

# 32. Spectral / Wavelet Features

Event bursts often have frequency structure.

Event intensity signal:

[
x(t)=N_{events}(t,t+\Delta t)
]

Fourier energy:

[
E_f = |\mathcal{F}(x)(f)|^2
]

Wavelet transform:

[
W(a,b)=
\int x(t)\psi_{a,b}(t)dt
]

Use this to detect:

* quote stuffing-like bursts
* cancel waves
* sweep preparation
* auction transition
* liquidity pulses before macro/news events

This is not a primary alpha by itself. It is a regime/anomaly detector.

---

# 33. Extreme Value Theory for Liquidity Shocks

Model tail events in cancellation waves, sweeps, and spread expansions.

For shock variable (X), above threshold (u):

[
P(X>u+x|X>u)
\approx
\left(
1+\frac{\xi x}{\beta}
\right)^{-1/\xi}
]

Shock score:

[
ShockScore =
P(X_t > x | X_t > u)
]

Useful for:

* detecting abnormal cancellation waves
* avoiding liquidity holes
* stress-testing models
* trade manager kill/size-down logic

---

# 34. Bayesian Regime Filter

Define hidden regimes:

[
R_t \in
{
calm,
directional,
sweep,
absorption,
toxic,
fragile,
auction,
news
}
]

Update:

[
P(R_t|E_{1:t})
\propto
P(E_t|R_t)P(R_t|E_{1:t-1})
]

Feature packet to regime model:

[
E_t =
[
NOFI,
CancelImb,
TradeImb,
MicroDrift,
Spread,
EventRate,
Entropy,
VacuumScore,
AbsorptionScore
]
]

This gives the trade manager a clean state label instead of hundreds of raw features.

---

# 35. Change-Point Detection

Use CUSUM for abrupt transitions.

[
S_t^+ =
\max(0,S_{t-1}^+ + x_t-\mu_0-k)
]

[
S_t^- =
\min(0,S_{t-1}^- + x_t-\mu_0+k)
]

Trigger if:

[
S_t^+ > h
\quad \text{or} \quad
S_t^- < -h
]

Apply to:

* event rate
* OFI
* cancellation rate
* spread
* microprice drift
* liquidity entropy
* book-shape distance

This catches regime shifts before normal bar-based models adapt.

---

# 36. Final Alpha Feature Families

For HFT3, I would not dump all formulas into one monster model. I would create feature families.

| Family               | What It Captures                        |
| -------------------- | --------------------------------------- |
| Static depth         | book shape now                          |
| Dynamic OFI          | book pressure changes                   |
| Queue position       | fill probability and priority           |
| Age/survival         | durable vs fake liquidity               |
| Cancellation         | liquidity retreat                       |
| Replenishment        | absorption and hidden liquidity         |
| Entropy              | concentration of orders                 |
| Hawkes intensity     | self/cross-exciting event bursts        |
| Queue-reactive state | next move probability                   |
| Cross-asset OFI      | leading market pressure                 |
| Adverse selection    | whether a fill is dangerous             |
| Latency haircut      | whether signal survives execution delay |
| Regime filter        | when feature family is valid            |
| Extreme events       | when to size down or stop               |
| Path signatures      | event-order dependency                  |

---

# 37. The Most Valuable MBO Calculations

If I had to rank them:

| Rank | Calculation                 | Why It Matters                      |
| ---: | --------------------------- | ----------------------------------- |
|    1 | Queue depletion probability | Direct next-tick move logic         |
|    2 | Multi-level OFI             | Better than static imbalance        |
|    3 | Cancellation imbalance      | Early warning before movement       |
|    4 | Fill probability            | Converts alpha into executable edge |
|    5 | Adverse selection score     | Prevents toxic passive fills        |
|    6 | Replenishment/absorption    | Detects hidden passive strength     |
|    7 | Age-weighted liquidity      | Separates real from fake depth      |
|    8 | Entropy/concentration       | Detects fragile walls               |
|    9 | Hawkes event intensity      | Predicts bursts/cascades            |
|   10 | Cross-asset OFI             | Captures leading pressure           |

---

# 38. What the Structured Packet Should Look Like

Do **not** give the LLM raw MBO.

Give it a fixed packet:

```text
MBO_FEATURE_PACKET

schema_version: "1"
packet_schema_version: "mbo_feature_packet_v1"
packet_id:

instrument:
  instrument_id:
  symbol:
  venue:
  data_class: "L3_MBO"
  source:

timestamp_ns:
receive_timestamp_ns:
latency_budget_us:
spread_ticks:
event_rate_10ms:
event_rate_100ms:
event_rate_1s:

depth:
  imbalance_1:
  imbalance_5:
  imbalance_10:
  exp_decay_imbalance:
  order_count_imbalance_10:
  age_weighted_imbalance_10:
  entropy_bid_10:
  entropy_ask_10:
  order_concentration_score:
  wall_quality_score_bid:
  wall_quality_score_ask:
  cumulative_depth_slope:
  depth_curvature:
  depth_convexity:
  book_shape_distance:
  wasserstein_regime_distance:

microprice:
  level_1_ticks:
  multi_level_ticks:
  drift_vs_mid_ticks:

flow:
  ofi_10ms:
  ofi_100ms:
  ofi_1s:
  normalized_ofi_10ms:
  normalized_ofi_100ms:
  normalized_ofi_1s:
  mlofi_level_count:
  mlofi_unit: "contracts"
  mlofi_side_convention: "bid_add_or_sell_cancel_positive"
  mlofi_vector:
  cancel_imbalance:
  trade_imbalance:
  add_imbalance:
  imbalance_velocity:
  imbalance_acceleration:
  cancel_acceleration:

queue:
  bid_depletion_probability:
  ask_depletion_probability:
  volume_ahead_bid:
  volume_ahead_ask:
  volume_behind_bid:
  volume_behind_ask:
  queue_rank_bid:
  queue_rank_ask:
  queue_front_imbalance:
  expected_fill_time_bid:
  expected_fill_time_ask:
  passive_fill_probability:
  adverse_selection_probability:

liquidity_quality:
  bid_absorption_score:
  ask_absorption_score:
  iceberg_score_bid:
  iceberg_score_ask:
  replenishment_score_bid:
  replenishment_score_ask:
  displayed_depth_resistance_bid:
  displayed_depth_resistance_ask:
  fleeting_liquidity_bid:
  fleeting_liquidity_ask:
  fragile_wall_score_bid:
  fragile_wall_score_ask:
  vacuum_score_bid:
  vacuum_score_ask:

event_model:
  hawkes_buy_trade_intensity:
  hawkes_sell_trade_intensity:
  hawkes_cancel_bid_intensity:
  hawkes_cancel_ask_intensity:
  queue_reactive_up_probability:
  queue_reactive_down_probability:

cross_asset:
  leading_ofi_refs:
    - instrument_id:
      lag_ms:
      ofi:
      confidence:
  cross_impact_score:
  lead_lag_confidence:

execution:
  raw_edge_bps:
  latency_adjusted_edge_bps:
  post_ev:
  take_ev:
  cancel_ev:
  authority: "advisory_only"
  advisory_signal:
  confidence:

audit:
  point_in_time_safe: true
  no_execution_authority: true
  source_doc_ids:
  feature_family_codes:
```

That is how MBO becomes usable by the research engine and, later, trade-manager analysis. The packet is advisory feature state only: it must not route, submit, cancel, replace, or promote orders by itself.

---

# 39. Clean Developer Prompt

```text
Implement an MBO feature-research layer that treats Market-by-Order data as an order-level event stream, not merely deeper MBP data.

Objective:
Build deterministic, timestamp-safe, point-in-time MBO feature extraction on top of the reconstructed order book. Do not redesign the robustness pipeline. Do not add new execution gates. The output should be a structured MBO_FEATURE_PACKET that existing models, robustness tests, and trade-manager analysis can consume as advisory state only. It must not route, submit, cancel, replace, promote, or override gates.

Required book state:
For every event, reconstruct the live order-level book using order_id, side, price, size, action, priority/sequence, and timestamps. Maintain per-level bid/ask size, order count, order age, queue position, and order lifecycle state.

Required feature families:
1. Static depth imbalance:
   - imbalance_1, imbalance_5, imbalance_10
   - weighted imbalance using exponential level decay
   - order-count imbalance
   - age-weighted imbalance

2. Microprice:
   - level-1 microprice
   - multi-level microprice
   - microprice drift versus mid

3. Order flow imbalance:
   - OFI over event windows and clock windows
   - multi-level OFI vector
   - normalized OFI
   - imbalance velocity
   - imbalance acceleration

4. Queue analytics:
   - volume ahead
   - volume behind
   - queue rank
   - queue-front imbalance
   - expected fill time
   - passive fill probability
   - bid/ask depletion probability

5. Cancellation analytics:
   - cancel rate by side/level/window
   - cancellation imbalance
   - cancel acceleration
   - liquidity vacuum score

6. Replenishment and absorption:
   - bid absorption score
   - ask absorption score
   - same-level replenishment after aggressive trades
   - displayed-depth resistance score

7. Hidden-liquidity / iceberg candidate score:
   - repeated same-price fills
   - displayed-size refresh behavior
   - same-order-id refresh where venue semantics support it
   - rapid re-adds after execution
   - executed volume exceeding visible displayed depth

8. Fleeting-liquidity risk:
   - add-then-cancel-without-fill ratio
   - short-lifetime order ratio
   - side-specific fleeting-liquidity imbalance
   - fragile wall score

9. Entropy and concentration:
   - per-level order-size entropy
   - top-K book entropy
   - order concentration score
   - wall quality score using size, age, entropy, and cancellation probability

10. Book-shape geometry:
   - cumulative depth slope
   - curvature
   - convexity/concavity
   - liquidity gap/vacuum metrics
   - Wasserstein or comparable book-shape distance versus historical regime templates

11. Event intensity models:
   - rolling event intensities by event type
   - Hawkes-style self/cross-excitation features for add/cancel/trade events
   - queue-reactive state features and next-move probability estimates

12. Adverse selection and execution value:
   - expected fill probability
   - expected adverse move after fill
   - post_ev
   - take_ev
   - cancel_ev
   - latency-adjusted edge using current event intensity and configured latency budget

13. Cross-asset pressure:
   - cross-instrument OFI
   - lead-lag score
   - cross-impact score
   - options delta/gamma-weighted pressure where options data exists

Validation requirements:
All features must be point-in-time safe. No feature may use book state after the decision timestamp. Use exchange/matching-engine timestamp for market sequencing where available and preserve receive timestamp for latency diagnostics. Every feature must support event-time windows and clock-time windows. Output must be deterministic and reproducible from raw MBO replay.

Output:
Produce a fixed-schema MBO_FEATURE_PACKET per instrument per decision timestamp. The packet must be compact, stable, and consumable by the existing HFT3 robustness pipeline without adding new gates or redesigning the pipeline.
```

The key point: **MBO’s edge is not “more levels.” It is order birth, aging, priority, cancellation, replenishment, and death.** That is where the extra signal lives.

[1]: https://databento.com/docs/schemas-and-data-formats/mbo "Market by order (MBO) | Databento schemas & data formats"
[2]: https://arxiv.org/abs/1011.6402 "[1011.6402] The Price Impact of Order Book Events"
[3]: https://www.cmegroup.com/articles/faqs/market-by-order-mbo.html "Market by Order (MBO) - CME Group"
[4]: https://arxiv.org/abs/1502.04592 "[1502.04592] Hawkes processes in finance"
[5]: https://arxiv.org/abs/1312.0563 "[1312.0563] Simulating and analyzing order book data: The queue-reactive model"
[6]: https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf "LimitOrderBookRevised.dvi"
