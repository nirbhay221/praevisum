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

**Collaborative Partner** - live dialogue, clarifying questions, adapting. **12 entries** vs Taskmaster's 35, where all four direct competitors sit (valence, hostpilot, lead-recovery-agent, S2PNexus).

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
| Compute | Cloud Run |
| State | Firestore - customers, stock, technicians, work orders, complaint history |
| Retrieval | Vector search over complaint text |
| Events | Pub/Sub |
| Watch | Cloud Scheduler (commitment keeper) |
| Channels out | WhatsApp (Twilio), email |

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
| Domain model + seeded dealer data | `VERIFIED` | 5 brands, 3 sites, 3 techs, 6 seeded repairs |
| Tools (identify, history, stock, dispatch, work order, promise, briefing) | `VERIFIED` | `scripts/smoke.py` runs the full chain with no credentials |
| **Briefing from repair history** | `VERIFIED` | Correctly surfaces both parts, excludes van stock |
| **Closed learning loop** | `VERIFIED` | `scripts/loop.py` - new fault, technician writes it down, a different unit at a different site recalls it from different words |
| Semantic recall (TF-IDF cosine, offline) | `VERIFIED` | 0 keyword overlap, still recalled at score 0.219 |
| Fitment guard on suggested parts | `VERIFIED` | Recall crosses manufacturers, parts do not. Caught a Whirlpool board being sent to a Traulsen |
| Proximity dispatch (haversine + drive time) | `VERIFIED` | Technicians ordered by drive time from the site, not just skills |
| Work-order close + transcript capture | `VERIFIED` | Releases unused reservations back to stock on close |
| Vertex AI RAG corpus behind the same interface | `NOT BUILT` | `VertexRagRepairIndex` is a stub; `LocalRepairIndex` is the working one |
| ADK memory service on the Runner | `NOT BUILT` | Runner currently gets a session service only |
| Promise refusal on unavailable part | `IMPLEMENTED` | Reserve-or-refuse logic written, not yet exercised on the contended SKU |
| Agent graph (front / parallel lookups / parts) | `IMPLEMENTED` | Builds and wires; not yet run against a model |
| Twilio to LiveRequestQueue bridge | `IMPLEMENTED` | Written, never carried a real call |
| Audio transcode (mulaw 8k / PCM 16k / PCM 24k) | `IMPLEMENTED` | Continuous resampler state, untested against real audio |
| Live inbound call loop end to end | `NOT BUILT` | Needs GCP project + Twilio number + tunnel |
| Equipment specialist (per-brand manuals) | `NOT BUILT` | |
| Commitment keeper | `NOT BUILT` | |
| WhatsApp + photo intake | `NOT BUILT` | |
| Eval / ablation harness | `NOT BUILT` | The persuasive one. Same calls, briefing off vs on |

## Not yet on GitHub

Deliberately local until we decide. Repo must be created **during** the submission period anyway.
