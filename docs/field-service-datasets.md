# Equipment that needs a site visit, and where the public data is

The test for inclusion is one question:

> **Does a human travel to the broken thing, having already decided what to bring?**

If yes, a wrong decision costs a wasted trip, and a briefing is worth sending.
If no (a TV, a router, a phone), the thing is replaced rather than repaired and
there is nothing to save.

That test includes enterprise laptops under onsite warranty, which I initially
and wrongly excluded. Dell ProSupport is same or next business day onsite after
a remote diagnosis, and Lenovo and HP run the same model. The first BBB
complaint that surfaces on the subject is Dell dispatch sending the wrong parts
and the field engineer failing to complete the job. That is this product's
exact problem in another industry.

---

## Verified public, pulled live, no key required

### ENERGY STAR certified products - `data.energystar.gov`
**278 distinct companies, 17,117 models.** Socrata API, CSV/JSON, also on data.gov.
Per model: brand, real model number, product type, defrost type, refrigerant,
doors, volume, daily kWh, dimensions, certification date.

| Dataset | Models | Brands | Socrata id |
|---|---|---|---|
| Light Commercial HVAC | 11,994 | 22 | `e4mh-a2u3` |
| Commercial Refrigerators & Freezers | 1,931 | 72 | `wati-2tfp` |
| Laboratory Grade Refrigerators | 1,076 | 51 | `g242-ysjw` |
| Commercial Ice Machines | 590 | 36 | `nak5-fsjf` |
| Commercial Dishwashers | 418 | 29 | `pk8q-dim8` |
| Commercial Ovens | 370 | 49 | `c8av-ccf7` |
| Hot Food Holding Cabinets | 235 | 17 | `wyw6-sr4d` |
| Commercial Steam Cookers | 200 | 12 | `vtsv-aq9u` |
| Commercial Fryers | 175 | 29 | `edi8-b5vk` |
| Commercial Griddles | 47 | 5 | `nw5s-r5ca` |
| Commercial Coffee Brewers | 19 | 2 | `6xa2-5c2t` |

Also published there: commercial boilers, commercial water heaters, commercial
clothes washers, vending machines, water coolers.

**Biggest companies by certified model count**
HVAC: Lennox 1,745 - Allied Commercial 1,715 - Carrier 806 - Kenmore 724 -
Bryant 723 - Airquest 710 - Arcoaire, Comfortmaker, Day & Night, Heil, Keeprite,
Tempstar 695 each - Maratherm 491 - ACIQ 478
Commercial kitchen: IDW 300 - True Refrigeration 258 - Beverage-Air 175 -
Traulsen 145 - Turbo Air 112 - Continental 111 - Victory 59 - Hoshizaki 47 -
Delfield 46 - Imbera 39 - Structural Concepts 34 - Everest 33 - EFI 31 -
Atosa 31 - Metalfrio 24 - U-Line 24 - Fogel 22 - Supera 20

### FDA device registration - `api.fda.gov/device/registrationlisting.json`
Every registered medical device manufacturer and their listed devices. Free,
no key, openFDA. Hospital equipment is serviced onsite by manufacturer field
engineers, so this is a large field-service industry with an open dataset.

**By listed device count:** STERIS 11,113 - Sotera Health 10,504 -
Medtronic 5,128 - Intuitive Surgical 3,578 - Stryker 3,367 - Zimmer Biomet 3,108 -
Arthrex 2,999 - Karl Storz 2,486 - Philips Medical Systems 2,400 -
Medline 2,331 - Cardinal Health 2,257 - DeRoyal 2,244 - Abbott 2,193 -
GE HealthCare 2,147 - Smith & Nephew 1,994 - Integra LifeSciences 1,992 -
Teleflex 1,946 - Boston Scientific 1,923 - Merit Medical 1,767 - Jabil 1,710
(1,000+ firms on the first page alone)

Related and also free: FDA 510(k) clearances, device recalls, adverse events
(MAUDE), Unique Device Identification database.

### CPSC - `saferproducts.gov/RestWebServices/Recall`
Recalls with manufacturer, model numbers and date codes. Verified working:
16 refrigeration recalls including U-Line outdoor freezers (fire hazard),
Electrolux and Frigidaire, Viking built-ins, Haier chest freezers, Galanz.
Directly useful - a technician approaching a unit should know it is under an
active safety recall.

### NHTSA vPIC - `vpic.nhtsa.dot.gov/api`
All vehicle and vehicle-equipment manufacturers, free, no key. Also FARS crash
data, recalls, complaints, investigations, and VIN decoding. Dealer service
departments are field service with the same first-visit-fix problem.

### AHRI Directory - `ahridirectory.org` (HTTP 200, browsable)
Certified HVAC and refrigeration performance ratings. No open API found; would
need scraping. Overlaps ENERGY STAR but covers more equipment classes.

---

## Field-service industries with the same shape, data status

| Industry | Onsite service | Public dataset |
|---|---|---|
| Commercial kitchen equipment | yes | **ENERGY STAR, verified** |
| HVAC, commercial and light commercial | yes | **ENERGY STAR 11,994 models, verified** |
| Medical and imaging equipment | yes, manufacturer field engineers | **openFDA, verified** |
| Commercial refrigeration | yes | **ENERGY STAR, verified** |
| Enterprise IT hardware under onsite warranty | yes, Dell/Lenovo/HP | no product registry found |
| Elevators and escalators | yes, Otis, Kone, Schindler | state registries, not a national open set |
| Agricultural and construction equipment | yes, dealer techs | partial via NHTSA equipment |
| Forklifts and material handling | yes | none found |
| ATMs and kiosks | yes, NCR, Diebold | none found |
| Copiers and production print | yes, Xerox, Ricoh, Canon | none found |
| Generators and power equipment | yes, Cummins, Generac | partial |
| Laboratory instruments | yes, Thermo Fisher, Agilent | partial via FDA for regulated ones |

---

## What no public dataset contains anywhere

**Parts catalogues.** Encompass has a REST API but needs credentials plus a
net-terms trade account; credit-card accounts are refused. Marcone is the same
through EPASS.

**Repair histories.** What actually broke, what the technician found, and which
part fixed it. No government, manufacturer or industry body publishes this. It
exists in technicians' heads and in dealers' private systems, and it leaves when
they retire.

That absence is the product. If this were downloadable somebody would already
have built it. The only way to obtain it is to capture it as a byproduct of the
work, which is why the technician closes a job by replying to a text rather than
filling in a form.
