# SkyLink: 3-minute Pareto pitch (script)

_AI-drafted speaking notes. **Rewrite in your own words before presenting.** About 420 words, roughly 3 minutes at a calm pace. The slide for each part is in brackets._

**0:00 – The problem** [`figures/m4_networks.png`]
Kaveripuram has 24 health centres. Blood and antivenom take one and a half to three hours by road today. We were asked to design a drone network that gets emergencies there within 45 minutes, at the lowest annual cost we can defend.

**0:25 – What we optimised** [`figures/m6_pareto.png`, panel a]
Every dot on this plot is a full system: a vehicle, a hub network, a fleet and a dispatch policy. Each one was simulated hour by hour against the district's real wind. We traded three things against each other: annual cost, the share of emergencies delivered within 45 minutes (S45), and the share of orders that still go by road. NSGA-II found this front from about 8,500 simulated configurations. A weighted sum lands on or behind it, which is our check.

**0:55 – The knee** [same plot, knee marked]
Moving right to left, money buys service fast at first. Then the curve flattens, and that bend is the knee: three hubs, 11 drones, 14 spare batteries, one drone held back for emergencies. It costs ₹2.25 crore a year, 40 % of what sending everything by road would cost, and 89 emergencies in 100 arrive within 45 minutes. Spending ₹20 lakh more buys nothing measurable.

**1:25 – Why not 90 %** [`figures/m7_robustness.png`]
Our target was 90 in 100, and no point on the front reaches it over a year. The reason is wind, not fleet size. Gusts ground every drone for about 5 % of the year's hours, and for a quarter of them in July. A calm week gives 97 in 100. The worst monsoon week gives 53, and on those days the road vehicle is part of the system.

**1:55 – Is the choice robust?** [`figures/m7_reliability.png`, `figures/m7_sensitivity.png`]
We ran 500 random months, with demand 25 % up or down and up to 10 % of drones broken. The knee reaches 90 in 100 in about three months in ten. Demand and breakdowns hardly matter, because the weather decides. Under minimax regret, the knee is the safest choice whenever a late emergency is worth more than about ₹5,300.

**2:25 – What would change our mind**
One lever changes the answer. Batteries 20 % better, in the same drone, lift service to 94 in 100 and lower cost, because fewer orders go by road. Cheaper drones save ₹16 lakh a year. If orders grow 15 % a year, emergency service holds and the cost of routine deliveries climbs. Adding drones and batteries each year keeps the network under the cost ceiling for five years.

**2:50 – The ask**
Approve the three-hub network at ₹2.25 crore a year, keep the road backup staffed in the monsoon, and write better batteries into the purchase.
