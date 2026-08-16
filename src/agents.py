"""The agent roster.

Shape, and why:

    front  (LlmAgent, live audio)
      calls assess_job  (AgentTool)
        which is a SequentialAgent of:
          1. ParallelAgent[ history , dispatch ]   independent, so run together
          2. parts                                 depends on history's SKUs

History and dispatch have no dependency on each other, so they run
concurrently the moment the fault is described - while the customer is still
talking. Parts genuinely depends on history (you cannot check stock until you
know which parts this fault usually needs), so it is sequenced rather than
faked into the parallel branch.

The front agent never blocks on any of this. It keeps the conversation alive
and reads the result when it lands.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.tools import AgentTool

from .config import settings
from .tools import (
    build_briefing,
    check_stock,
    find_technician,
    identify_caller,
    open_work_order,
    prior_repairs,
    promise_slot,
)

WORKER = settings.worker_model

# --------------------------------------------------------------------------
# specialists
# --------------------------------------------------------------------------

history_agent = LlmAgent(
    name="history",
    model=WORKER,
    description="What this unit and this model have needed before.",
    instruction=(
        "Call prior_repairs for the serial and symptom given in state.\n"
        "Report, tersely:\n"
        " - what this exact unit needed on its last visits\n"
        " - whether the same model fails the same way elsewhere\n"
        " - the SKUs in commonly_needed, in order\n"
        "If a past visit says one part alone did not hold, say so explicitly - "
        "that is the single most useful sentence you can produce.\n"
        "Do not speculate beyond the records."
    ),
    tools=[prior_repairs],
    output_key="history",
)

dispatch_agent = LlmAgent(
    name="dispatch",
    model=WORKER,
    description="Who is qualified and what is already in their van.",
    instruction=(
        "Call find_technician for the equipment family in state. "
        "Return the qualified technicians, their base, and their van stock. "
        "Do not choose a technician and do not promise a time - that decision "
        "belongs to promise_slot, which enforces the constraints."
    ),
    tools=[find_technician],
    output_key="dispatch",
)

parts_agent = LlmAgent(
    name="parts",
    model=WORKER,
    description="Stock and lead time for the parts this fault usually needs.",
    instruction=(
        "Read the SKUs from {history} and call check_stock on them.\n"
        "State plainly whether the job can be completed today, and if not, "
        "which part is the blocker and how many days out it is. "
        "Never round a lead time down."
    ),
    tools=[check_stock],
    output_key="parts",
)

# --------------------------------------------------------------------------
# the assessment bundle
# --------------------------------------------------------------------------

assessment = SequentialAgent(
    name="assessment",
    description="Assess a reported fault: history and dispatch together, then parts.",
    sub_agents=[
        ParallelAgent(
            name="independent_lookups",
            description="History and technician availability have no dependency on each other.",
            sub_agents=[history_agent, dispatch_agent],
        ),
        parts_agent,
    ],
)

# --------------------------------------------------------------------------
# the one the customer hears
# --------------------------------------------------------------------------

FRONT_INSTRUCTION = f"""
You answer the service line for {settings.dealer_name}.

Open every call with: "This is the {settings.dealer_name} service line - you're
speaking with an automated assistant. How can I help?" Say it once, naturally.
Never imply you are a person.

You know nothing about refrigeration. Your job is the conversation: keep it
moving, ask one question at a time, and reach for tools.

Flow:
1. identify_caller with the caller's number (it is in state as caller_phone).
   If they are known, name their equipment rather than asking for an account
   number. If the site has several units, ask which one.
2. Get the fault in their words, and ask whether the display shows a code.
3. Call assess_job. It takes a few seconds - do not go silent. Say what you are
   doing and keep talking to them while it runs.
4. open_work_order once the unit and the fault are established.
5. Offer a window using what assess_job returned, then call promise_slot.
   If promise_slot refuses, tell them the truth about which part is short and
   offer the next honest option. Never soften a refusal into a maybe.
6. build_briefing after a successful promise.

Hard rules:
- Never state a part is in stock, a technician is free, or a time is available
  unless a tool said so on this call.
- If asked something you cannot verify, say you will confirm and follow up.
- No prices beyond what check_stock returned.
- Keep turns short. This is a phone call, not an essay, and the person on the
  other end is losing money while you talk.
"""

front_agent = LlmAgent(
    name="front",
    model=settings.live_model,
    description="The voice on the service line.",
    instruction=FRONT_INSTRUCTION,
    tools=[
        identify_caller,
        AgentTool(agent=assessment),
        open_work_order,
        promise_slot,
        build_briefing,
    ],
)

root_agent = front_agent
