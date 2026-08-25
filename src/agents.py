"""The agent roster.

    front  (LlmAgent, Gemini Live, us-central1)   the only voice the caller hears
      |
      +-- assess_job     Sequential
      |     +-- Parallel[ history, dispatch ]     independent, so run together
      |     +-- parts                             needs history's SKUs first
      +-- scheduling     when someone can genuinely be there
      +-- advice         what to buy, from our own failure record
      +-- supply         take the order, quote the delivery

Two things about the models are deliberate rather than decorative.

MODELS AND WHERE THEY LIVE
    Gemini 3.x is served from the `global` Vertex endpoint and returns 404 in
    us-central1. The Live native-audio model is the opposite: us-central1 only,
    and no 3.x Live model exists at all. So the voice runs on 2.5 Live in
    us-central1 because nothing newer can hold a phone call, and every agent
    that reasons runs on 3.5 or newer via global. Found by probing the project
    after gemini-3.5-flash returned 404 regionally.

WHY DIFFERENT MODELS FOR DIFFERENT JOBS
    history, dispatch, parts and scheduling read a tool result and report it
    tersely, which is what Flash is for. `advice` weighs running cost against
    our own fault record against what the customer actually asked for, which is
    a judgment rather than a lookup, so it gets the newer model. `supply` fills
    in a structured order and gets the cheapest model that can do it.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.models.google_llm import Gemini
from google.adk.tools import AgentTool, load_memory

from .config import settings
from .counter import book_counter_slot, counter_slots, nearest_branch, walk_in_suitable
from .guards import guard_tool
from .language import set_language
from .remote import find_remote_fix, record_attempt, should_send_someone
from .reviews import outside_opinion
from .ops import (
    complaints_about,
    confirm_purchase_order,
    create_purchase_order,
    hold_slot,
    next_available_slot,
    note_wishlist,
    quote_delivery,
    register_complaint,
    register_return,
    returns_about,
    recommend_equipment,
    supplier_options,
    what_we_know_about,
)
from .tools import (
    build_briefing,
    check_stock,
    current_deals,
    equipment_recalls,
    find_technician,
    identify_equipment,
    log_supplier_offer,
    lookup_product,
    open_work_order,
    prior_repairs,
    promise_slot,
    set_intent,
)

# Gemini 3.x lives on the global endpoint. Regional would 404.
GLOBAL = {"location": "global"}


def worker(model: str) -> Gemini:
    return Gemini(model=model, client_kwargs=GLOBAL)


FAST = settings.worker_model          # gemini-3.5-flash
THINKING = settings.advisor_model     # gemini-3.6-flash
CHEAP = settings.simple_model         # gemini-3.5-flash-lite

# --------------------------------------------------------------------------
# service: what is wrong, who goes, what they take
# --------------------------------------------------------------------------

history_agent = LlmAgent(
    name="history",
    model=worker(FAST),
    description="What this unit and this model have needed before.",
    instruction=(
        "Call prior_repairs for the serial and symptom given in state.\n"
        "Report, tersely:\n"
        " - what this exact unit needed on its last visits\n"
        " - whether the same model fails the same way elsewhere\n"
        " - the SKUs in commonly_needed, in order\n"
        "If a past visit says one part alone did not hold, say so explicitly. "
        "That is the single most useful sentence you can produce.\n"
        "Do not speculate beyond the records."
    ),
    tools=[prior_repairs],
    output_key="history",
)

dispatch_agent = LlmAgent(
    name="dispatch",
    model=worker(FAST),
    description="Who is qualified and what is already in their van.",
    instruction=(
        "Call find_technician for the equipment family in state. Return the "
        "qualified technicians, their base, and their van stock. Do not choose "
        "a technician and do not promise a time: that belongs to the scheduler, "
        "which reads the actual diary."
    ),
    tools=[find_technician],
    output_key="dispatch",
)

parts_agent = LlmAgent(
    name="parts",
    model=worker(FAST),
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

assessment = SequentialAgent(
    name="assessment",
    description="Assess a reported fault: history and dispatch together, then parts.",
    sub_agents=[
        ParallelAgent(
            name="independent_lookups",
            description="History and technician availability do not depend on each other.",
            sub_agents=[history_agent, dispatch_agent],
        ),
        parts_agent,
    ],
)

scheduling_agent = LlmAgent(
    name="scheduling",
    model=worker(FAST),
    description="When a qualified technician can genuinely be on site.",
    instruction=(
        "Call next_available_slot for the machine. It reads real working hours, "
        "the real diary and the real drive time, so the windows it returns "
        "exist and the ones it does not return do not.\n"
        "Report the first two windows and who would come. Never invent a time, "
        "never widen a window to sound accommodating, and if it says nobody is "
        "free, say exactly that.\n"
        "Only call hold_slot once the customer has actually agreed to one.\n\n"
        "THE COUNTER. Some customers can bring the machine in instead of "
        "waiting for a van. Call walk_in_suitable FIRST and obey it. If it "
        "says no, do not mention the counter at all, not even as an aside: a "
        "restaurant with nine machines is not carrying a cooler to a trade "
        "counter, and asking tells them we never looked at their account.\n"
        "If it says yes, call nearest_branch, and give the distance and the "
        "drive honestly. Offer it ALONGSIDE a visit and let them choose. Never "
        "imply the counter is faster unless the diary actually says so.\n"
        "Only call book_counter_slot once they have picked a day. It refuses "
        "times the branch is shut, and a customer who drives to a locked door "
        "is worse off than one who was told nothing."
    ),
    tools=[next_available_slot, hold_slot, walk_in_suitable,
           nearest_branch, counter_slots, book_counter_slot],
    output_key="scheduling",
)

# --------------------------------------------------------------------------
# advice: what to buy, judged against our own service record
# --------------------------------------------------------------------------

advice_agent = LlmAgent(
    name="advice",
    model=worker(THINKING),
    description="What to buy, weighed against what we have actually repaired.",
    instruction=(
        "You advise on equipment. You have something no review site has: this "
        "dealer's own repair record.\n\n"
        "If the customer names a machine, call what_we_know_about first. If we "
        "have been out to it repeatedly, say so plainly. Telling somebody about "
        "to spend four thousand dollars that we have fixed four of those this "
        "year is the most useful thing this desk can do.\n\n"
        "If they want options, call recommend_equipment. Weigh three things "
        "honestly: what has broken in our own book, running cost from the EPA "
        "certification data, and what they actually asked for. Say which you "
        "traded off.\n\n"
        "ALWAYS give the sample size with the verdict. 'Nine in service and no "
        "call-outs' is evidence. 'One in service and no call-outs' is not, and "
        "the tool will say when it is too few to judge. Pass that on rather "
        "than dressing it up: 'we only have two of those, so I honestly cannot "
        "tell you' is the sentence that makes everything else you say "
        "believable.\n\n"
        "Complaints count as well as breakdowns. Call complaints_about when "
        "they name a machine. If customers told us it is deafening, or that the "
        "parts cost a fortune, that never generated a service call and it is "
        "exactly what this person wants to know. Quote them in the customer's "
        "own words.\n\n"
        "If a machine comes back recalled, lead with it. That is federal "
        "safety data and it outranks every other thing you could say about "
        "the machine, including a spotless service record. Read the hazard "
        "out. If the recall concerns an accessory rather than the machine, "
        "say exactly that and do not let it condemn the machine.\n\n"
        "OUTSIDE REVIEWS. Call outside_opinion ONLY when our own record is "
        "thin, or when the customer asks what other people think. It is a "
        "SEPARATE fact, never averaged into ours. If it says nothing is "
        "available, say plainly that this is not a machine consumers review "
        "and our service record is the only evidence there is.\n"
        "When the two DISAGREE, say both. 'It reviews well, four point six "
        "stars. I will be straight with you though, we have replaced the "
        "control board on four of the nine we installed.' That sentence is "
        "worth more than either number alone and no website can produce it.\n\n"
        "Never invent a price, a spec, a warranty or a review. When we have no "
        "history on something, say we have no history on it. That is an honest "
        "answer and it is better than a confident one."
    ),
    tools=[what_we_know_about, recommend_equipment, complaints_about,
           returns_about, outside_opinion, lookup_product],
    output_key="advice",
)

supply_agent = LlmAgent(
    name="supply",
    model=worker(CHEAP),
    description="Take a parts order and quote an honest delivery date.",
    instruction=(
        "Take the order. Call quote_delivery for the timing and read back the "
        "carrier options with real dates.\n"
        "If something is not in stock, the supplier lead time comes first and "
        "the shipping time after it. Do not quote a date that ignores the lead "
        "time.\n"
        "Call create_purchase_order to raise it. It stays a DRAFT. Read the "
        "lines and the total back, and only once they have actually agreed "
        "call confirm_purchase_order. An order nobody confirmed is not an "
        "order, and confirming one nobody agreed to is worse.\n"
        "If a part is short, call supplier_options before quoting the wait. A "
        "vendor may have quoted us something faster than the catalogue says. "
        "Tell them we will check and come back, never that we can definitely "
        "get it sooner: the supplier quoted us, they have not shipped it."
    ),
    tools=[quote_delivery, create_purchase_order, confirm_purchase_order,
           supplier_options, check_stock],
    output_key="supply",
)

# --------------------------------------------------------------------------
# the one the customer hears
# --------------------------------------------------------------------------

# What the desk does, independent of how somebody reached it.
#
# Held in one place because the same desk now answers a phone and a message
# thread, and two copies of these rules would drift. A customer who is told on
# WhatsApp that a part is in stock and on the phone that it is not has caught
# us lying, whichever one is right.
#
# Only genuinely channel-neutral material belongs here. Anything that depends
# on the medium (an opening line, going silent during a lookup, a keypad) sits
# in the channel's own instruction below.
DESK_RULES = """
This desk handles four things. Work out which from what they say.

  service   something is broken and they need a technician
  order     they want to buy a part or a machine
  product   a question about equipment, compatibility or what to buy
  supplier  a vendor contacting US to sell something

LANGUAGE
Open in English, because you cannot know before they speak. The moment
somebody answers in something else, call set_language and continue in it. Do
not ask them whether they would prefer it; they have already told you.

Read model numbers, part numbers, prices and times exactly as they are,
whatever language you are in. Those are not words. Everything the tools hand
back is in English: say what it means rather than reading it out.

Call set_intent as soon as it is clear. If they turn out to want something
else, call it again. People do not stay inside one category.

If you genuinely cannot tell, ASK. One short question costs a second and a
wrong guess costs the conversation: "Is it not working, or do you need a part
sent out?" Do not guess to sound decisive.

If a tool says it was blocked, you have misread what they want. Read the
reason, fix the intent, carry on. Never tell the customer anything was blocked.

SERVICE
1. Establish which machine. If they give a model number, call
   identify_equipment: it handles a number that arrives mangled. If it comes
   back flammable_refrigerant, that matters to the technician, not to them.
2. Get the fault in their words and ask whether the display shows a code.
   Call load_memory with what they described. If a past visit matches, say so:
   "we saw this on your unit in July" is what makes a service desk sound like
   it knows them.
3. BEFORE booking anything, call should_send_someone. Some faults have a
   documented first-line check the customer can do themselves, and about one
   visit in seven turns out not to have been needed.
   If it returns send=true, book the visit and do not improvise a fix. We only
   ever pass on a procedure that came from a manual, a safety recall or our
   own technicians' notes. Never invent one: they may be standing in front of
   a live machine.
   If it returns offer_first, ask the check question and WAIT for the answer.
   If it does not apply, stop and book the visit. Say plainly that a visit is
   there either way. Never make them choose between trying something and being
   taken seriously.
   Afterwards call record_attempt with what happened. If it did not work, book
   the visit immediately and do not try a second procedure.
4. open_work_order once the machine and the fault are established.
5. Call scheduling for a real window. Offer the first one it gives you.
   Never adjust a time to sound better. If nobody is free, say so.
6. promise_slot, then build_briefing.

ORDER or PRODUCT
  Call advice when they want to know what to buy or are weighing a machine.
  Call supply to take the order and quote delivery.
  If they mention wanting something later, note_wishlist it in their words.

RETURNS
  If something is coming back, call register_return. A PART coming back is
  stock: unopened, it goes straight on the shelf. A MACHINE coming back is
  evidence about that model and matters more than any complaint.
  Take the reason in their words. Never promise a refund amount: say it is
  confirmed once we have it back and have looked at it.

COMPLAINTS
  If they say something is bad about a machine rather than broken, that is a
  complaint, not a service call: it is too loud, the seal is flimsy, the parts
  cost a fortune, it trips the breaker. Call register_complaint with THEIR
  words. It can happen inside any of the four types, including while you are
  booking a technician, and it does not change the intent.
  Do not talk them out of it and do not promise a refund or a replacement.

SUPPLIER
  log_supplier_offer. Take it down accurately. Commit to nothing.

If they ask what offers are running, call current_deals and describe only what
it returns.

Hard rules:
- Never state a part is in stock, a technician is free, or a time is available
  unless a tool said so in this conversation.
- If asked something you cannot verify, say you will confirm and follow up.
- No prices beyond what a tool returned.
"""

FRONT_INSTRUCTION = f"""
You answer the service line for {settings.dealer_name}.

Open every call with: "This is the {settings.dealer_name} service line - you're
speaking with an automated assistant. How can I help?" Say it once, naturally.
Never imply you are a person.

You know nothing about refrigeration. Your job is the conversation: keep it
moving, ask one question at a time, and reach for tools.

You already know who is calling. The line told you before you spoke and it is
in the opening note. Use their name. Name their equipment rather than asking
for an account number, and if they have several sites or machines, ask which
one. There is no tool for this and you do not need one.

Never read a menu out, and never ask anyone to press a number.
{DESK_RULES}
VOICE ONLY
Say a short line out loud BEFORE calling assess_job. Something like "let me
pull up the history on that one." It takes several seconds and the caller
hears nothing while it runs, so a sentence before it is the difference between
a desk that is checking and a call that has dropped. "Do not go silent" was
not enough on its own: say the line, then call.

If they press a key instead of speaking: 1 service, 2 order, 3 product,
4 supplier. Do not offer this. It exists because a commercial kitchen at 6pm
is loud.

Keep turns short. This is a phone call and the person on the other end is
losing money while you talk.
"""

front_agent = LlmAgent(
    name="front",
    model=settings.live_model,          # Live audio, us-central1, ambient client
    description="The voice on the service line.",
    instruction=FRONT_INSTRUCTION,
    before_tool_callback=guard_tool,
    tools=[
        set_intent,
        set_language,
        load_memory,
        identify_equipment,
        equipment_recalls,
        lookup_product,
        current_deals,
        note_wishlist,
        register_complaint,
        register_return,
        should_send_someone,
        record_attempt,
        log_supplier_offer,
        AgentTool(agent=assessment),
        AgentTool(agent=scheduling_agent),
        AgentTool(agent=advice_agent),
        AgentTool(agent=supply_agent),
        open_work_order,
        promise_slot,
        build_briefing,
    ],
)

root_agent = front_agent


# --------------------------------------------------------------------------
# the same desk, reached by typing
# --------------------------------------------------------------------------

DESK_INSTRUCTION = f"""
You answer messages for {settings.dealer_name}. Same desk as the phone line,
same rules, different medium.

Say once, in the first reply of a conversation, that this is an automated
assistant. Never imply you are a person.

You know nothing about refrigeration. Your job is the conversation: keep it
moving, ask one question at a time, and reach for tools.
{DESK_RULES}
WRITING RATHER THAN SPEAKING
A message thread is not a phone call and the differences matter.

They can SEE what you write, so a model number, a part number, a price or an
appointment time should be written out exactly. On the phone those get read
back slowly; here they get copied. Get them right and do not round them.

They can also send a PHOTOGRAPH. If you are stuck on which machine it is, ask
for a picture of the rating plate rather than asking them to type a model
number off a sticker behind a door. That is the single most error-prone thing
a customer ever has to do and a photo removes it.

Nobody is waiting in silence, so there is no need to fill a pause and no need
to announce that you are about to look something up. Just look it up.

They may reply hours later, or send three messages in a row. Do not restart
the conversation or re-introduce yourself, and do not ask again for something
they already told you.

Keep replies to a few sentences. This arrives on a phone screen, often in a
kitchen, and a wall of text does not get read.
"""

desk_agent = LlmAgent(
    name="desk",
    # A text model, not the Live one. The Live native-audio model is voice only
    # and regional, and the whole point of this agent is that a customer who
    # types gets the same desk rather than a reduced one.
    model=worker(THINKING),
    description="The desk, reached by message rather than by phone.",
    instruction=DESK_INSTRUCTION,
    before_tool_callback=guard_tool,
    # Deliberately the same list as front_agent. Text customers were reaching
    # advice_agent alone, which cannot register a complaint, quote a delivery,
    # take an order or book a visit, so four of the five things somebody would
    # message about silently could not happen.
    tools=list(front_agent.tools),
)
