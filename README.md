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
- **Inbound with disclosure only. No outbound sales calling.**

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

1. Phone rings on speaker. Real conversation, stressed restaurant owner, walk-in failing.
2. Nameplate photo arrives on WhatsApp mid-call; agent resolves model and serial.
3. Agent commits to a slot **while the customer is still on the line**.
4. Cut to the technician's phone - briefing lands with three prior occurrences on that unit and the parts to load.
5. **Break it:** the part gets allocated to another job; agent calls back and re-commits.
6. Ten-second generality beat: second call, Whirlpool, `dF`, different manual, same engine.
7. Close on the GCP console - Cloud Run, Firestore, Pub/Sub.

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

## Not yet on GitHub

Deliberately local until we decide. Repo must be created **during** the submission period anyway.
