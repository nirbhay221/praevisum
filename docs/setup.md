# Everything this project needs

## Already on your machine

| | |
|---|---|
| Python | 3.12.3 OK (`audioop` still present; 3.13 would need `audioop-lts`) |
| gcloud SDK | 474.0.0 OK - **old, run `gcloud components update`** |
| Docker | 26.0.0 OK |
| git | 2.51.0 OK |
| node | 20.12.2 OK |
| gcloud account | `nirbhaymalhotra2@gmail.com` |
| gcloud project | `driven-copilot-420615` (existing - make a fresh one for this) |
| ngrok | not installed, **and probably not needed** (see Public URL) |

---

## 1. Google Cloud - the only mandatory cloud

### Project and billing
- A GCP project. Make a clean one: `praevisum-<something>`.
- **Billing must be enabled** - Vertex AI refuses to serve without it, even on credits.
- Apply the **$150 hackathon credits** to this project.

### APIs to enable
```
aiplatform.googleapis.com        # Vertex AI - Gemini + Live API
run.googleapis.com               # Cloud Run
firestore.googleapis.com         # state
pubsub.googleapis.com            # commitment events
cloudscheduler.googleapis.com    # commitment keeper tick
secretmanager.googleapis.com     # Twilio creds
cloudbuild.googleapis.com        # deploy from source
artifactregistry.googleapis.com  # image storage
```

### NOTE: Region is a hard constraint
The Live API native-audio model is **`us-central1` in the US**. Set the project region to `us-central1` and leave it there. Wrong region means the socket simply never opens, and it will not be obvious why.

Model id: `gemini-live-2.5-flash-preview-native-audio-09-2025`
(GA on Vertex as of I/O 2026, production SLAs, multi-region failover.)

### Auth
Local dev - no key file needed:
```
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
gcloud config set run/region us-central1
```
Cloud Run - attach a service account with `roles/aiplatform.user`, `roles/datastore.user`, `roles/pubsub.editor`, `roles/secretmanager.secretAccessor`. No JSON key anywhere.

### Env
```
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=<project id>
GOOGLE_CLOUD_LOCATION=us-central1
```

> There is a shortcut - a **Gemini API key** from AI Studio (`GOOGLE_API_KEY`) skips all of the above and works for local dev. Do **not** ship on it: the judging criteria include *visible Google Cloud deployment*, and an API key shows nothing. Use it for a fast first light only.

---

## 2. Twilio - the phone line

| Need | Detail |
|---|---|
| Account SID | `TWILIO_ACCOUNT_SID` |
| Auth Token | `TWILIO_AUTH_TOKEN` |
| Phone number | Voice-capable US local, **~$1.15/month** |
| Media Streams | Included, no extra signup - this is what `/stream` consumes |
| Inbound voice | ~$0.0085/minute |

NOTE: **Trial accounts will ruin the demo.** A Twilio trial can only call verified numbers and prepends a recorded trial message to every call. Upgrade (minimum ~$20 top-up) before recording anything.

**Voice webhook** on the number: `https://<your-cloud-run-url>/voice`, method POST.

### WhatsApp (only for the photo-intake day)
Twilio WhatsApp **sandbox** is free - you join with a code from your own phone. Fine for a demo. A production sender needs Meta approval and is not worth it here.

---

## 3. Public URL - you likely don't need ngrok

Cloud Run gives a stable `https://` URL that upgrades to `wss://` for free, which is exactly what Twilio Media Streams needs. Deploying from source is one command:

```
gcloud run deploy praevisum --source . --region us-central1 --allow-unauthenticated
```

Then `PUBLIC_WS_BASE=wss://praevisum-xxxxx-uc.a.run.app`.

Use ngrok only if the deploy loop gets annoying while debugging audio. Free tier gives a new random URL each restart (you'd re-point the Twilio webhook every time); a static domain is ~$8/month.

---

## 4. Free, no key required

| Source | Use |
|---|---|
| **SaferProducts.gov API** | CPSC public consumer complaint database - real appliance fault reports |
| **CPSC Recalls API** | Recall status per model |
| Whirlpool diagnostic codes / tech sheets | `CF`, `PO`, `dF` etc. Free PDFs |
| **Traulsen Master Service Manual, Form TR35705** | INTELA-TRAUL controllers, G-Series and R&A Series. Free PDF |
| iFixit troubleshooting trees | Symptom to component |

These are the equipment-specialist's knowledge. PDFs go straight into Gemini's 2M context.

## Not getting, deliberately

**Encompass parts API** needs Encompass-issued credentials *plus a net-terms trade account*; credit-card accounts are refused outright. Marcone is the same via EPASS. Both are impossible inside two weeks. The parts catalog stays seeded, behind the adapter boundary in `src/domain/store.py` where Encompass would drop in.

---

## 5. Submission

- Devpost account, registered for the hackathon
- GitHub repo - public, **or** private shared with `testing@devpost.com` and `cloudhackathons@google.com`
- YouTube or Vimeo, public, ~4 minutes
- README with setup steps + architecture diagram
- Commit as **Nirbhay Malhotra `<nirbhaymalhotra2@gmail.com>`**

---

## 6. What it will actually cost

Live API audio is ~25 tokens per second, at **$3/1M input** and **$12/1M output**.

A four-minute call where each side talks about half the time:

| | |
|---|---|
| Caller audio in | ~2,500 tokens, **$0.008** |
| Agent audio out | ~2,500 tokens, **$0.030** |
| **Per call** | **~$0.04** |

So **200 test calls ≈ $8**. Worker models (`gemini-2.5-flash`) for the specialists are rounding error. Firestore, Pub/Sub and Cloud Run stay inside free tiers at this volume.

**Realistic total: under $30, comfortably inside the $150 credits.** The only real money is the Twilio upgrade (~$20) and the number (~$1.15/month).

Cost discipline that matters: **develop the agent logic in text mode, not audio.** Audio output is 4× the price of input and 100× the price of text. Only switch to voice when testing the call itself.

---

## 7. Order to do it in

1. `gcloud components update`
2. New GCP project, billing on, credits applied
3. Enable the eight APIs, set region `us-central1`
4. `gcloud auth application-default login`
5. `.env` from `.env.example`, Vertex vars filled
6. **First light:** get `run_live()` producing audio from a local script before touching Twilio. If this fails, everything downstream is unreachable
7. Twilio account, upgrade, buy number
8. `gcloud run deploy --source .`, point the Twilio voice webhook at `/voice`
9. Call the number
