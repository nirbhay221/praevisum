# Praevisum

*prae-VEE-sum* &middot; Latin, **foreseen**.

**One phone number. Four businesses behind it. The caller never hears about that.**

A refrigeration dealer, an IT reseller, an office furniture supplier and an
audio-visual company share a service desk. Somebody rings about a freezer, a
laptop, a chair or a projector and is served by whichever of the four sells it,
without being transferred, put on hold, or told "that is a different
department". The same desk answers WhatsApp. The same desk remembers what it
sold them last month.

Built for the **All Things Agentic Hackathon**.

**System architecture:** [the drawing](https://claude.ai/code/artifact/3c54a6d2-de63-400f-9d78-60cae3e540b9)
&middot; [as a PDF](docs/architecture.pdf)
&middot; [written out](docs/architecture.md)

---

### Notices

All manufacturer names, model numbers and diagnostic codes are used
nominatively to identify compatible equipment. No affiliation with,
sponsorship by, or endorsement from any manufacturer is claimed or implied.
**All customers, technicians, sites, work orders and repair records in this
repository are fictional** and exist to demonstrate the system.

---

## What happens when somebody rings

The desk answers by name, because it knows the number. It works out which of
the four businesses sells what they are asking about, and moves the call there
without saying so. From then on there are two roads.

### They want to buy something

It reads out what is actually on the shelf, at their price, in order. If this
business does not stock it but another of the four does, the call moves across
and the caller only notices that the answer arrived.

Before the order is placed it says what the maker's warranty covers. When the
maker's terms are not on file, it offers cover of our own instead of saying "I
do not know" - and where that cover would cost more than a fifth of the
purchase, it tells them not to buy it.

The order is read back with the machine, the cover and the real carrier
options, all on one invoice, and nothing is placed until they say yes.

### Something is broken

It finds which of **their** machines they mean, from what they said, without
ever asking for a model number or an account reference. We have those. They do
not.

It works out whether a visit is even warranted, or whether it can be talked
through on the call. If somebody has to go, it finds an engineer who is
qualified and near, checks whether the blocking part is already in their van,
and offers a real slot from the diary. When nobody can be sent it says so and
escalates, rather than inventing a date.

The engineer gets the briefing by email: what this exact unit needed last time,
what the same model does elsewhere, what the customer photographed, and what to
put in the van. They text back what they actually fitted, and the book learns
from it.

---

## What it does that a phone system does not

**It knows what our own vans have seen.** Recommendations are ranked by what
has broken in our service record, not by review scores. A model with repeated
faults in our own book is a bad recommendation however well it sells, and where
there are too few in service to judge, the desk says so instead of guessing.

**It tells people not to buy things.** Extended cover on a chair a maker
already covers for twelve years is selling somebody nothing. The desk declines
it and says why.

**It knows what each product costs after the sale.** Service visits, returns
and warranty claims are posted against the model that caused them. A product
that sells well and costs more in service than it earned is not a reorder, it
is a delisting - and the restocking advice refuses to buy more of it.

**It knows whether its own advice was any good.** What the engineer was told to
take and what they actually fitted are both recorded. Parts fitted that we
never named are the ones that cause a second visit, and that number is
reported separately from the cheap failure of loading a van with things nobody
used.

**It asks permission properly.** An artificial voice making a marketing call
needs written consent, so agreeing on the phone is not enough. The desk texts,
and only a reply grants it. Asked once, after a sale or a complaint, and a no
stands forever.

---

## The part worth arguing with

A language model asked to carry a fact across a boundary will drop it. Not
often enough to notice in testing, and often enough to matter on a Tuesday
afternoon. Every serious fault this system has had was that:

- an order number, so a freezer bought this afternoon was confirmed against a
  standing desk from an hour earlier
- a price, so cover quoted at $22.60 went onto the invoice at $45.00
- a position in a list, so "the third one" bought a chair that had never been
  read out
- a machine, so a customer who rang about their own laptop was offered a
  stranger's, and then asked to read out a model number for something we had
  sold them three hours before

The answer is not a better prompt. What was said, what was offered, what they
chose and which order this conversation raised are all kept where the model
cannot lose them, and checked in code before every single tool call. That layer
is the reason the desk can be trusted with a price.

The same layer keeps the four businesses apart. No caller has ever been quoted
one company's stock from another's shelf, and that is enforced rather than
hoped for.

---

## Where the data comes from

Nothing here is invented for a demo except the customers themselves.

| | |
|---|---|
| **88,544** equipment models | US Energy Star certification data, with efficiency and refrigerant |
| **923** products on the shelf | real retail listings, with real prices |
| **851** closed repairs | the searchable record of what actually fixed what |
| **757** machines | on 165 customer accounts across the four businesses |
| **19** engineers | with certifications, van stock and home bases |
| Federal recall data | so a recalled machine is never recommended without saying so |

---

## Running it

```
pip install -r requirements.txt
python scripts/load_all.py          # build the book
uvicorn src.main:app --reload       # the desk and the console
```

The console is at `/console`. Point a phone number's voice webhook at
`/voice` and its messaging webhook at `/whatsapp`.

Configuration lives in `.env`: model credentials, the phone account, and the
mail server the engineer briefings go out through. Everything has a working
default except the credentials.

---

## Honest caveats

**Advice takes about half a minute.** Weighing our own repair record against
the catalogue is several model calls, and the caller hears music while it runs.
The music is honest and it is not an answer.

**Machines can be registered twice.** A customer describing something they
already own can produce a second record of it, and an engineer certified on
"laptop" is not certified on "notebook". The family matching is too literal.

**The book is one file.** SQLite on one machine. Right for four dealers, wrong
for forty, and the schema ports without changes.

**Warranty terms are held for far fewer makes than we sell.** Offering our own
cover when the maker's terms are unknown is honest, and it papers over a gap
rather than closing it.

**Rate limits degrade a call mid-sentence.** When the model provider throttles
us the desk retries once and plays music rather than silence. That is all it
can do, and it is visible in the transcripts.

---

## Layout

```
src/            the desk, the console, and everything behind them
  agents.py     who answers, and who they hand work to
  guards.py     the checks that run before every tool call
  buying.py     orders, pricing, delivery
  our_cover.py  our own protection plans
  ledger.py     what each product costs us after the sale
  service_loop.py  what we advised, what was fitted, what it cost
docs/           architecture, data sources, setup
scripts/        loading the book, one-off migrations, checks
tests/          1,003 of them
```
