# Praevisum

*prae-VEE-sum* &middot; Latin, **foreseen**.

**A technician drives an hour and doesn't have the part. The company already knew which part it was.**

Praevisum answers the service line live, works out what the fault is while the customer is still describing it, commits to a window on the call, and sends the technician the one thing nobody ever sends them: what this exact failure needed the last three times, and what to put in the van.

Then the technician writes down what it actually was, and the next call knows more than this one did.

Built for the **All Things Agentic Hackathon** (Google). Deadline **2026-08-31, 5:00pm PDT**.

Full research, competitive analysis and decision history: `../all-things-agentic-NOTES.md`

---

### Notices

All manufacturer names, model numbers and diagnostic codes are used nominatively to identify compatible equipment. No affiliation with, sponsorship by, or endorsement from any manufacturer is claimed or implied. **All customers, technicians, sites, work orders and repair records in this repository are fictional** and exist to demonstrate the system.

---

## The number this exists for

Aberdeen, on why first service visits fail:

| Cause | Share |
|---|---|
| **Insufficient or incorrect parts on site** | **51%** |
| Technician lacked required skills/training | 25% |
| Insufficient time allocated | 13% |

A failed first visit means **2.7 total visits**, **+13 days** to resolution, and **34% higher** cost. 25% of all service calls need a second trip. A truck roll runs $250-500, near $1,000 fully loaded (TSIA).

**Half of all failed service calls are the wrong parts in the van.**

## Domain

Commercial refrigeration and kitchen equipment service. You are the service and parts dealer; the caller is a restaurant owner at 6pm whose walk-in is failing.

- $2,000-$10,000 of spoiled inventory from one walk-in failure
- $1,000-$5,000/day lost revenue if it closes the kitchen
- $500-$2,000/hour in lost sales for a busy restaurant
- A real **4-hour rule** for walk-in coolers - a ticking clock on camera
- *"If a part needs to be ordered, you could be down 1-3 business days"* - so the parts decision at intake **is** the outcome

## The flow

1. Customer calls. Agent **answers live and converses** - identifies them from the number.
2. Customer describes the problem, or sends a **photo on WhatsApp mid-call**. Gemini resolves it to a model and serial.
3. Parts question: stock and ETA. Service question: keep going.
4. Agent checks technician availability and skills, **commits to a slot while the customer is still on the line**.
5. **The differentiator:** pulls every prior complaint on that model and fault, what was done, which parts were *consumed* - and sends the assigned technician a briefing **with the parts to bring, before they leave**.
6. Confirmation out on the customer's channel.
7. **The commitment stays owned.** Part reallocated or morning job overruns, it re-negotiates and tells everyone.

## Agents

| Agent | Job |
|---|---|
| **Front** | The only one the customer hears. Natural conversation, never dead air. Knows nothing about refrigeration on purpose. |
| **Router** | Hears the complaint, wakes the right specialist. Traulsen and Whirlpool load different manuals. |
| **Equipment specialist** (per family) | Loaded with that brand's service docs and error codes. Symptom to likely components. |
| **History** | What this unit and this model have done before; parts *consumed*, not ordered. |
| **Parts** | Stock, ETA, substitutes. "Fixed today or down three days?" |
| **Dispatch** | Who's free, who's qualified, how far, what time can honestly be promised. |
| **Briefing writer** | What the technician receives before leaving. |
| **Commitment keeper** | Runs after the call. Re-negotiates when the promise breaks. |

**Why it must be multi-agent:** a natural conversation allows ~300ms before a pause sounds wrong; a parts + dispatch + history lookup takes seconds. Specialists run in the background *while the customer is still talking*. The front agent never stalls because it never waits.

## Track

**Taskmaster.**

Devpost's wording for it: *"a complete workflow, not just a chatbot"*, an agent
that *"takes action"* on a *"messy, multi-step chore"* and can *"send the right
info to the right places"* **"without constant user direction"**.

That is this system. A customer rings, and nine steps later a technician has a
booked slot, reserved parts and a briefing. Separately, a daily sweep nobody
starts looks for people worth ringing.

Collaborative Partner was the earlier pick, on field size. It was dropped after
reading Devpost's own examples for it: an agent that *"quizzes you as you go and
learns which concepts you struggle with"*, or one that *"learns your brand
preferences from your corrections"*. Both are one user, taught over sessions.
A caller who rings twice a year about a freezer is a different shape, and the
learning here improves the system from many callers rather than the
relationship with one.

---

## Hackathon rules that constrain us

### Mandatory (a floor, not a ceiling)
1. **Gemini 3.5 or newer** via Gemini API or Vertex AI
2. **One Google agent framework** - ADK, GenAI SDK, Antigravity SDK, or GenKit. **We use ADK**
3. **At least one Google Cloud service** - Cloud Run, Cloud SQL, Firestore, GKE, Pub/Sub

### Explicitly allowed
- **Third-party APIs and SDKs are permitted**, provided we're authorized under their terms. **Twilio is fine**
- Other models may be used alongside Gemini
- **Gemma / Veo / Lyria earn bonus points (up to 0.6)**
- Standard frameworks, libraries, starter templates, AI coding assistants

### Constraints to respect
- NOTE: **Project must be newly created during the submission period (Aug 3 - Aug 31).** Any pre-existing code incorporated **must be disclosed**. No silent reuse of Lymerorium / Proveracus code.
- Public or private repo; private must be shared with `testing@devpost.com` and `cloudhackathons@google.com`
- README with setup instructions, architecture diagram, ~4 min demo video showing Google Cloud deployment
- IP stays ours; Google gets a perpetual, irrevocable, worldwide, royalty-free, non-exclusive licence for evaluation and promotion

### Legal - before anything outbound
- TCPA treats AI-generated voice as "artificial or prerecorded voice"; outbound to residential/wireless generally needs prior express consent
- Several states require an **AI-voice disclosure at the start of the call**
- **Illinois is two-party consent** - relevant to recording the demo; use consenting participants
- $500/violation, trebled to $1,500 willful

#### Approaching a business we have never met

The earlier rule here read "inbound with disclosure only, no outbound sales
calling". That was the safe reading rather than the correct one, and the
researched position is narrower in one direction and wider in another.

- The **Telemarketing Sales Rule broadly exempts marketer-to-business calls**, and the national Do Not Call registry does not reach them. This is the entire legal basis for `prospect.py`.
- The exemption **stops at the handset**. Every wireless number is treated as residential whoever owns it, and there is no business carve-out for a mobile. So an AI voice may ring a published business landline and may not ring a mobile. `linetype.py` resolves which, and fails closed to "mobile" on every error path.
- **Calls 9:00 to 19:00 local time at their address**, which is narrower than the law allows on both ends.
- An **internal do-not-call request** is a separate obligation from the federal registry, survives the end of any relationship, must be honoured within 10 business days, and the record is kept 4 years. `take_us_off_your_list` is on both desks; the row is never deleted.
- **Messaging is not a way round any of it.** WhatsApp requires explicit prior opt-in and treats an imported list as grounds for shutting the sender down. A Telegram bot cannot open a conversation at all: the user must message it first. Both platforms are opt-in by construction, so there is no cold channel in any medium.

The practical consequence is that most small restaurants publish a mobile and
are therefore unreachable by this desk. On the seeded demonstration set, two of
five businesses may lawfully be rung. That ratio is the feature.

---

## Stack

| Layer | Choice |
|---|---|
| Model | Gemini 3.5 (Vertex AI) - Gemini Live for the audio loop - multimodal reads the nameplate photo |
| Framework | **Google ADK** (Python) |
| Telephony | Twilio inbound, WebSocket media stream |
| Compute | Compute Engine VM. **Not Cloud Run**: a call holds a websocket open for its whole length, and scaling to zero mid-call drops the customer |
| State | SQLite in production, with a working Cloud SQL Postgres backend behind `PRAEVISUM_DB_BACKEND`. **Not Firestore**: 36 tables, 7 views, foreign keys and cross-table transactions |
| Retrieval | Vertex `text-embedding-005` over the repair corpus, one index per dealer, word-overlap fallback |
| Events | Pub/Sub for briefings and outreach, behind `PRAEVISUM_BUS`. In-process SSE for the console |
| Watch | systemd timer running the outreach sweep daily |
| Channels out | SMS. WhatsApp and email not built |

### What ADK gives us
Open-source agent execution framework (Python / Java / Go / TypeScript), supports A2A.
- **Agents + Tools** - rich tool ecosystem, custom code, MCP via custom install scripts
- **Sessions** - every conversation is a Session tracking *events* and *state*
- **Runners** - the loop: agent yields an Event, the runner processes it, updates the session, resumes the agent
- **Memory** - short-term in-app (keyword match, lost on restart) and long-term via **Vertex AI RAG corpus** with semantic search; storage backend swappable without touching agent code
- **Deployment** - Vertex AI Agent Engine (managed runtime, evaluation, Sessions, Memory Bank), or Cloud Run / GKE

---

## Data

**Real and free:**
- **BLS Occupational Employment and Wage Statistics** - occupation 49-9021, refrigeration mechanics, at metro level rather than national. Davenport-Moline-Rock Island 2025: mean $33.65/hr (series `OEUM001934000000049902103`), median $31.34 (`...08`). This is what the labour line on every quote is built from, and the series ID is recorded on the quote so the number can be pulled again rather than defended
- **Manufacturer warranty statements** - True, Traulsen, Continental, Beverage-Air, Avantco, Delfield, Hoshizaki. Loaded by `scripts/load_warranties.py` with the source URL and the day it was read, because the terms differ per series and they change: Traulsen's six year term applies to units invoiced from January 2023 and not before
- **SaferProducts.gov API** - CPSC public consumer complaint database (appliance reports of harm), searchable and exportable
- **CPSC Recalls API**
- **Brand service documentation** - Whirlpool diagnostic codes (`CF` main and UI board comms failure, `PO` power outage, `dF` defrost failure), tech sheets, iFixit trees; **Traulsen Master Service Manual Form TR35705** (INTELA-TRAUL controllers, G-Series and R&A Series). PDFs into Gemini 2M context.

**Gated - seed instead:**
- **Encompass** REST API (JSON, OpenAPI 3.0, SwaggerHub) requires Encompass-issued credentials **plus a net-terms trade account**; credit-card accounts refused. Marcone similar via EPASS.
- Seed the parts catalog behind a clean adapter boundary where Encompass would plug in.

**Brands to seed:** Whirlpool (residential), True, Traulsen, Beverage-Air, Hoshizaki (commercial).
Engine stays brand-agnostic: it keys on manufacturer, model and symptom or error code, finds the candidate components, then what previous calls actually fixed, then the parts they consumed.

NOTE: **Do not sprawl.** Proveracus grew to seven domains; the review verdict was *"the fix is LESS + rigor, not more."* General in the model, singular in the demo.

---

## Plan

| Days | Work |
|---|---|
| 1-3 | **Telephony + Gemini Live loop end to end.** Biggest risk - front-load it. |
| 4-6 | Firestore data model; seed operational data across five brands |
| 7-9 | Dispatch agent + technician briefing (**never cut**) |
| 10-11 | Commitment keeper: watch, re-negotiate, notify |
| 12 | WhatsApp channel + photo intake |
| 13 | Record demo (twice) |
| 14 | README, architecture diagram, write-up, submit - a day spare |

**Cut order:** outbound sales calls (never build), then photo intake, then the second-brand generality beat. **Never cut the briefing.**

## Demo (~4 min)

Lead with the refusal, not the conversation. Plenty of entries this year will
show an agent booking an appointment; the differentiator is a system that
declines, and says why in words a business owner would use.

**0:00 to 0:25 - one number, four businesses.**
Call the desk. It greets by naming what this caller owns, because the number
was resolved to an account before anybody spoke. Say something furniture: "the
gas lift on one of our chairs has gone". Same number, and the desk is now
answering as the furniture company, quoting its terms. Say the line out loud:
one number, four companies, and the caller never chose.

**0:25 to 1:20 - the breakdown, done properly.**
"The walk-in is not holding overnight, there is stock in it." Watch for three
beats: it puts the machine on the account rather than asking for an Asset ID,
it checks who is actually EPA certified for that refrigerant before promising
anybody, and it holds a slot without committing until they agree.

**1:20 to 2:05 - the part it will not sell you.**
Ask for a door gasket. It quotes **$78.20, not $92.00**, and says why: a live
15% offer it found in `promotion_parts` on its own. Then ask for something
recalled and watch it refuse. Then ask for `HL-L2400DW`: it finds the thirteen
`HL-L2400D` on the shelf and reports them as a NEAR match with what differs,
instead of selling the wrong printer.

**2:05 to 2:50 - the technician's side.**
The engineer texts a QUESTION, not a closure: "why is it producing hollow
cloudy cubes?" The answer comes back with the company's own history first,
counted: *"water inlet valve partially blocked, scale on the evaporator plate
(3 times, latest 2026-07)"*. Then they close by text, and the corpus grows
while you watch.

**2:50 to 3:35 - hunting, and mostly refusing to.**
Run the prospect sweep on screen. Five businesses, all five with a real
detected problem quoted from public text, and **two callable**. Read the
refusals out: two are mobiles, which an artificial voice may not ring whoever
answers, and one asked us to stop. This is the beat that lands, because
everybody else's outbound demo dials all five.

**3:35 to 4:00 - the receipts.**
The console: what the desk stopped this week, split into corrected and blocked.
Then `calibration.reliability()` returning `checked: 0` and saying so in its own
words. Close on that. A system that reports an empty result honestly is the
whole argument.

### What NOT to show

- Hold music. It is 32 seconds of instrumental and it proves nothing.
- The catalogue size. 88,544 machines is a number, not a capability.
- Anything requiring a live Serper or Twilio lookup, which costs money and can
  fail on camera. Seed it first with `python -m scripts.seed_prospects`.

---

## Honesty table

Stolen from `aish2897/after-hours-site-continuity-fleet`, which is a good pattern: nothing in this repo, the demo video, or the Devpost entry may claim a capability beyond what's recorded here.

| State | Meaning |
|---|---|
| `NOT BUILT` | Not built. |
| `IMPLEMENTED` | Code exists, local tests pass. Not exercised against real infrastructure. |
| `VERIFIED` | Exercised for real, with saved evidence. |

| Component | State | Note |
|---|---|---|
| **Live inbound call, end to end** | `VERIFIED` | Real call answered 2026-08-20. Journal shows the tool calls and the spoken greeting |
| Twilio to LiveRequestQueue bridge | `VERIFIED` | Carried that call |
| Audio transcode (mulaw 8k / PCM 16k / 24k) | `VERIFIED` | Same call. Continuous resampler state, no clicks |
| Websocket auth on `/stream` | `VERIFIED` | Live: no ticket 403, forged 403, expired 403, issued ticket accepted |
| Agent graph (front / parallel lookups / parts) | `VERIFIED` | Runs against real models |
| Domain model + generated dealer book | `VERIFIED` | 2 dealers, 420 machines over 48 models, 670 closed repairs |
| **Briefing from repair history** | `VERIFIED` | Expected-value van loading, with the money shown |
| **Briefing actually dispatched** | `VERIFIED` | Rendered and published to Pub/Sub; message pulled back and checked |
| **Closed learning loop** | `VERIFIED` | Technician texts back, Gemma parses it locally, the corpus grows |
| Semantic recall | `VERIFIED` | Vertex `text-embedding-005`, 670/670 embedded, per-dealer indexes |
| Multi-tenancy | `VERIFIED` | Two dealers, separate corpora, tests hunt for leaks |
| Fitment guard on suggested parts | `VERIFIED` | Recall crosses manufacturers, parts never do. Family guard too: a UPS gets no laptop parts |
| Proximity dispatch (haversine + drive time) | `VERIFIED` | Ordered by drive time, not just skills |
| Promise refusal on unavailable part | `VERIFIED` | Refused promise leaves nothing behind. Proven on SQLite and Cloud SQL |
| Trade counter + walk-in booking | `VERIFIED` | Regulars are never offered it; closed days and hours refused |
| Complaints as evidence | `VERIFIED` | Counted against a model with the denominator, quoted in the customer's words |
| Returns | `VERIFIED` | Unopened parts go back on the shelf and cut the reorder; returned machines count against the model |
| Restock advice | `VERIFIED` | Periodic-review reorder point, complaint signal discounted three ways |
| Federal recalls in buying advice | `VERIFIED` | Machine vs accessory recalls distinguished; recalled models cannot be recommended |
| **Outreach sweep (recalls, predictions, offers)** | `VERIFIED` | Daily systemd timer, runs with no human. Consent enforced, safety recalls exempt |
| Warranty | `IMPLEMENTED` | Absent entirely before this: no table, no column, no tool. The desk would quote four hundred dollars for a board on a covered machine and be confidently wrong in the direction that costs the customer money. Now driven by the manufacturers' own published terms, loaded with the URL they came from, and coverage is worked out PER LINE because that is how the terms actually read: wear items are excluded from every one of them, so a door gasket is chargeable on a fully covered machine; compressor cover outlasts parts and labour cover, so a six and a half year old Traulsen has a covered compressor and nothing else; and Traulsen ship the compressor and bill the owner for fitting it, so the part is free and the four hours are not. Not knowing is still reported as not knowing rather than as expired |
| What a visit costs | `IMPLEMENTED` | The money side was missing entirely: grep found zero references to a labour rate, a call-out charge or an out-of-hours premium. A visit recorded `labor_hours` after the fact and nothing priced them, so the first question anybody asks met the rule that there are no prices beyond what a tool returned and became "I will have to confirm and follow up", every time. `quote_visit` prices the labour from the BLS median wage for occupation 49-9021 in this metro, the hours from what jobs on that family actually took on our own book, and returns a range rather than one number. Every line carries what it is based on, and the quote is written down so what was said can be checked against what was billed |
| Certification, not just skill | `IMPLEMENTED` | `technician_skills` said somebody works on reach-in freezers. EPA Section 608 is what legally permits opening a refrigerant circuit, and its types are not interchangeable: Type I does not cover a walk-in. The briefing already warned that R-290 is flammable and could not say whether the person being sent was licensed to touch it |
| When the customer can be there | `IMPLEMENTED` | The diary knew when a technician was free and nothing asked when the restaurant could take one. A window offered across a lunch service gets refused, or worse accepted and missed. The scheduler now says WHY a slot was ruled out rather than only that nobody is free |
| Outbound calls actually placed | `IMPLEMENTED` | The last mile that did not exist. `sweep_recalls` found people who own a recalled machine, queued them above every sales call, and nothing rang anybody, which is worse than not sweeping because the system reported having handled it. Nothing in `outbound.py` decides who to ring: consent, quiet hours and the cap all ran in `outreach.py` first, asserted by a test that greps for them. Will not leave a voicemail about a recall, and a call that could not be placed is never marked done |
| Follow-ups actually delivered | `IMPLEMENTED` | The same gap as the recall queue, found in a second place. `followup.due()` rendered a missed call, a dropped call and an after-visit check into sentences and nothing read the list, so a customer whose call dropped got a message that never left the building while the desk recorded having followed up. `sender.py` tries the channel they actually use, falls through when one refuses, and leaves anything undelivered queued rather than marked sent |
| WhatsApp outbound | `IMPLEMENTED` | Every other WhatsApp path was a reply riding back in the TwiML. A follow-up has nobody to reply to, so it goes out over REST. Free inside Meta's 24 hour window, which is exactly when a dropped-call resume is sent |
| SMS inbound | `IMPLEMENTED` | `close_by_text` was built for a technician replying to a briefing by text and had no route to reach it, so the loop that grows the corpus only worked if they happened to be on WhatsApp |
| Sweep on Cloud Run | `IMPLEMENTED` | `Dockerfile.sweep`. The phone stays on the VM because a call holds a websocket and scaling to zero drops the customer; the sweep holds nothing, so it is the half that should scale to zero. Dialling is behind an explicit `--dial` so a deploy cannot start ringing people |
| Outbound call consumer | `IMPLEMENTED` | Claims a call, hands over the opening line, honours opt-out. Never dialled anybody |
| ADK memory service on the Runner | `VERIFIED` | `recall.py` implements `BaseMemoryService`, per-dealer |
| Cloud SQL Postgres backend | `VERIFIED` | Full schema applied, business logic run against it. Instance stopped to save cost |
| Pub/Sub | `VERIFIED` | Two topics exercised, then switched off |
| Owner console (prices, stock, offers, reorder) | `IMPLEMENTED` | Works; the agent itself is untested |
| Hold music during a lookup | `IMPLEMENTED` | Lyria track, 1.6s lead-in, cuts on speech. Never heard on a real call |
| Barge-in | `IMPLEMENTED` | Trusts `event.interrupted`, which has never been seen to fire |
| Vertex AI RAG corpus product | `NOT BUILT` | We embed and search ourselves; the managed product is unused |
| Outside reviews (Google Shopping) | `VERIFIED` | Run live against the real book. Model level, then brand level within the category, labelled which. Answers where the market answers (Brother 6,304, Lenovo 3,629, True 107) and stays quiet where it does not (Traulsen 19, Beverage-Air reach-ins single digits per model). Never blended with our own record |
| Text channels get the phone's context | `IMPLEMENTED` | A message thread now resolves the customer, their sites, machines and last visit before answering, exactly as the line does. `dealer_id` was the literal string `D-REF`, so an IT customer messaging in was answered out of the refrigeration book; it is derived from their account now. And a thread gets a `calls` row, so `review.py` can settle it and `patterns.py` can see it |
| Desk reachable from any channel | `IMPLEMENTED` | `desk.answer(identity, text, media)` is the whole brain; a channel only proves who called, downloads what it carried, and sends the reply. One copy of the rules, asserted shared by test |
| WhatsApp connector | `VERIFIED` | Signed webhook on the live server, fails closed with no token. A signed message came back with a real recommendation off the book in 20s, and a complaint written from a message landed in `complaints` as `CMP-986AC9`. Photo intake not yet exercised from a real handset |
| Telegram connector | `IMPLEMENTED` | Free, no verification, no join code. Secret-token webhook, fails closed. Weaker identity: no phone number, so a technician must `/link` once before they can close jobs, and nothing is guessed from a display name |
| Photo intake (data plate) | `IMPLEMENTED` | Vision transcribes the plate, the federal catalogue confirms it. An unrecognised reading is reported as unconfirmed rather than accepted, because a misread plate picks the wrong refrigerant |
| Email connector | `NOT BUILT` | Another pipe for words the phone already carries. Deliberately after WhatsApp, which adds a modality |
| Equipment specialist (per-brand manuals) | `NOT BUILT` | |
| Commitment keeper | `NOT BUILT` | |
| Missed calls | `VERIFIED` | The call row was written inside the media stream's `start` event, so a caller who hung up before it connected left no trace anywhere. `POST /call-status` records Twilio's verdict; a signed test created the row and queued a follow-up on the live server |
| Dropped-call resume | `IMPLEMENTED` | `review.settle` already knew a call had an intent and produced nothing; it now queues a message carrying the caller's own words back, so nobody reads a model number out a fourth time |
| After-visit check | `IMPLEMENTED` | One question a day after the job closes: is it holding. Not a satisfaction score, and never asked on the call, where the moment is wrong and the evidence is weaker than what the database already has |
| Customer memory that survives | `IMPLEMENTED` | `recall.py` claimed a conversation today was retrievable tomorrow and held it in a dict, so the loop it described died on every deploy and had never closed. Their words are in `caller_memory` now |
| What we learned about dealing with them | `IMPLEMENTED` | `knowing.py` derives habits from rows that already existed: photos that worked, times they repeated themselves, times we asked twice, and `channel_pref`, which had been stored on every contact since the first schema and read by nothing. Two conversations minimum, and nothing about how anybody likes to be spoken to, because there is no signal for it |
| Decision record, kept | `IMPLEMENTED` | Every decision written to `decisions`, tied to its call, with the figures stored apart from the sentence so "how often did we carry a part we did not need" is a query rather than parsing English. `GET /api/why?call=` answers why, weeks later. The trace was visible before it was durable, which was the wrong half to skip |
| Failure patterns | `IMPLEMENTED` | The half that was missing: `review.py` measured every call and nothing read it. Groups failures by the callers' own words until three of them become nameable. Deterministic, no model, so a reported pattern exists and an unreported one is genuinely not in the data. `GET /api/patterns` |
| Live reasoning trace | `IMPLEMENTED` | The arithmetic behind every decision, published while the caller is still talking. Both sides of each inequality, and the parts left behind as well as the ones taken. Publishes only, never computes, so a broken feed cannot change a decision (asserted by test) |
| Docker | `IMPLEMENTED` | Python 3.11 to match the VM. Database built from schema at image build, reference data deliberately not baked in. Verified the build step produces 43 tables and 10 views from the schema files alone; the image itself is not built here |
| Call review, all four flows | `IMPLEMENTED` | `calls.intent` and `calls.outcome` were designed in from the first schema and never written; both now are. Outcome is derived from the tables a call wrote, never declared by the agent. A call that ended with no van because a documented fix worked counts as a WIN, which is the opposite of how a bought containment metric scores it. Structural signals only, no sentiment. `GET /api/review` |
| Detecting the language, not being told | `IMPLEMENTED` | Opens in English because it cannot know before they speak, and switches the moment somebody answers in something else. `set_language` follows `set_intent`: session state for this call, and onto the contact so the NEXT one opens right. Carried to the retrieval by `guard_tool`, which already runs before every tool. Refuses a language nobody has thought about rather than pretending to switch |
| The caller's language | `IMPLEMENTED` | One in five US restaurant workers speaks English as a second language, and the person who finds the walk-in at twelve degrees at 6am is whoever opened up. Gemini speaking Spanish is configuration; the real problem is that 670 repairs are written in English, so a Spanish symptom retrieved nothing and the desk fell back to having no history silently. The symptom is normalised at the one boundary where it meets the corpus, model numbers and temperatures held behind placeholders, and their own words are what get quoted back. A test disables the normalisation and fails |
| Ordering from a supplier | `IMPLEMENTED` | The direction that was missing. `purchase_orders` is the customer buying from us; nothing recorded this dealer buying from a supplier, so "we knew and did not order" and "we ordered and it is late" were indistinguishable. Keeps what was advised beside what was actually bought |
| Machines in stock, not just parts | `IMPLEMENTED` | `restock_advice` read the `parts` table and nothing else, so the desk could recommend a Traulsen, weigh its running cost and quote delivery with no idea whether one was in the building. Separate table on purpose: a part is held because a missing one fails a call, a machine is held at capital cost against a sale that may not come |
| Service level by what it costs THEM | `IMPLEMENTED` | One 95% margin for every part was the wrong shape. A walk-in going down spoils thousands of dollars of stock; a printer does not. Critical families now sit at 99%, and a part fitting both is held at the higher one |
| Parts confirmed onto the van | `IMPLEMENTED` | The hole in this project's own opening line. The desk worked the part out, held it, and texted the briefing, and nothing checked anybody picked it up: `reservations` recorded a claim on stock, not a fact about a van. The briefing now asks for one word and the reply answers it, on the thread the technician already uses. A denial or a vague "ok" is never read as confirmation |
| Was the desk right | `IMPLEMENTED` | The desk says 44% and a technician later writes what it really was. Both facts always existed and were never compared, because the prediction was never written down. Reports the curve band by band with the sample behind each point, and corrects nothing: these are normalised retrieval similarities, and scaling them until they look right would produce a well calibrated number about a corpus that is generated |
| Eval / ablation harness | `NOT BUILT` | The persuasive one. Same calls, briefing off vs on |
| Ice machines findable at all | `VERIFIED` | `find_equipment` required `daily_kwh IS NOT NULL` and not one of 585 certified ice machines carries it, because ENERGY STAR rates them per 100 lb of ice rather than per day. Every ice machine had therefore been invisible to the only tool that recommends equipment, for the life of the project, and the desk answered "nothing in the catalogue matches" to anybody asking for one. Daily consumption is now computed from the machine's own published harvest rate and energy figure, which is a ceiling at full duty rather than an estimate. 1,134 rows backfilled, 578 findable, sized in pounds of ice a day because nobody asks an ice machine's cubic feet |
| Catalogue asked for what was said | `VERIFIED` | Three faults in one query. `daily_kwh` mixed commercial kWh-a-day with residential kWh-a-year in one `ORDER BY`; the family table fell back to `%` so "a freezer" matched the entire catalogue and "office chair" returned refrigerators; and rows with no model number were offered as the cheapest match, which is a machine the desk cannot name, quote or order |
| Variant model numbers | `VERIFIED` | `HL-L2400DW` could not match a stocked `HL-L2400D`, because a single LIKE cannot match a stored string SHORTER than the query. Thirteen sat on the shelf while the desk said we had none. Reported as a NEAR match carrying what differs, never merged into the exact results: the wireless one and the non-wireless one are different prices and only one does what they want |
| Offers applied at the quote | `VERIFIED` | The owner records promotions and `promotion_parts` maps them to exact SKUs. Nothing in any pricing path read that mapping, so a door gasket was quoted at the full $92.00 with a live 15% gasket offer on file, and the only route to it was already knowing to ask. Now applied unprompted and eligibility-gated. Offers that are not arithmetic, a buy-three-pay-for-two or free labour, are read out in their own words and never turned into a price |
| Learning loop reconciled | `VERIFIED` | The technician's text reply was the ONLY route into the corpus. 851 visits completed and diagnosed, 670 with a repair record: one job in five was done, written up and never reached anywhere the desk could read it. The bias had a direction, which is what made it worth fixing, since jobs closing on paper in the office are exactly the awkward ones worth learning from. Corpus 670 to 851, all re-indexed and retrievable, and it now runs BEFORE predictions in the nightly sweep |
| Guards keep a record | `IMPLEMENTED` | `guards.py` is where this product's central claim lives and it contained no `INSERT` of any kind: every interception was printed and discarded, so "it refuses rather than inventing" was true in the code and uncountable everywhere else. Now split into corrected, where the customer never noticed, and blocked, where the model was told no. Argument NAMES only, never values. Has not yet fired on a live call |
| Technician can ask, not only close | `IMPLEMENTED` | Every message from a known technician went straight into `close_by_text`, so an engineer texting "any idea why this keeps tripping the breaker?" had that parsed for a cause and a labour figure. Our own record comes back first and general trade knowledge second, never blended, and a sealed pressurised system is never walked through over text. Exercised against the live book, not yet over real SMS |
| Prospecting: finding businesses | `IMPLEMENTED` | The distress vocabulary is derived from our own 433 reported symptoms rather than written by hand, so it improves as the corpus grows. Width chosen by measurement: 40 terms caught 3 of 5 real faults with 0 false alarms, 120 caught 5 of 5 with 3. A miss costs a call never made; a false alarm means opening with "I gather you are having trouble" to a business that is not. **Has never touched the network** |
| Trade knowledge that ENFORCES rather than advises | `IMPLEMENTED` | Each vendor's trade note is injected into the instruction, and the furniture one says the single question deciding whether a recommendation is honest is how many hours a day a chair will be sat in. It advised and enforced nothing, so the desk could quote a task chair to a 24 hour dispatch office and nothing noticed until it failed with the warranty void for exceeding a duty rating. Now a before-tool gate: an order for a chair or a screen is refused until the question is answered, with the trade reason in the refusal so the desk can ask something sensible. Policy sits in `suitability.py` where a buyer can read it, enforcement in `guards.py`, which is the Policy Pack / Authorization Engine split from arXiv 2603.20953. Fails OPEN, unlike the ownership gate: this one protects the quality of a recommendation, not somebody else's data, and the softer rule must not be the one that can break a call. Two false matches were caught only by testing: a replacement gas lift resolving to "office chair" because `parts.families` records what a part FITS, and a real chair on the shelf resolving to nothing because the catalogue match was an equality test |
| Prospecting: the line-type gate | `IMPLEMENTED` | The TSR exempts marketer-to-business calls, and the exemption stops at the handset: no business carve-out exists for a wireless number. Fails closed to "mobile" on every path, including a missing credential and a timed-out lookup. Internal do-not-call is checked before the clock and before it will even spend money on a lookup, kept 4 years, never deleted. **No live Twilio lookup has been performed** |

### Honest caveats

- The dealer book is **generated**, not real. Machines and recalls are real
  public federal data; the customers, repairs, complaints and returns are not.
- The complaint-to-part signal measures **66%** against ground truth, but that
  ground truth is synthetic too. It shows the mechanism works, not that real
  complaints predict real failures.
- Outbound calling is built and **has never dialled anybody**. An AI voice is an
  "artificial or prerecorded voice" under the TCPA, so marketing calls require
  prior express written consent; the code enforces that and exempts safety
  recalls, which are not marketing.
- Prospecting has **never touched the network**. Every result shown comes from
  seeded rows and a seeded line-type cache, so it has cost nothing. A real
  sweep spends two searches per business plus a carrier lookup per number, and
  the flag that permits paying defaults to off precisely so that finding a
  prospect and spending money on one stay separate decisions.
- **Calibration is empty and honestly so.** `reliability()` returns
  `checked: 0`. Both `fault_distribution` decisions on file are the "nothing in
  our own history matches" branch, recorded from sweeps where there is no call
  to join through. The chain works; the desk has simply not yet taken enough
  real service calls that a technician later closed. No curve has been
  manufactured to fill the gap.
- Promotions carry **no machine-readable discount**. Percentages are parsed and
  applied; anything else is handed back as terms to read aloud, because a desk
  that works out "so that is about 30% off" has committed the business to a
  number nobody agreed.

## Not yet on GitHub

Deliberately local until we decide. Repo must be created **during** the submission period anyway.
