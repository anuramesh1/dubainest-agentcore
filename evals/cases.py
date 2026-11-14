"""Labelled cases. Expected intents are tuples: any one of them counts as correct
(a few questions are honestly ambiguous, e.g. "is 8% yield realistic?")."""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.prompts import CONFIRM_PHRASE


@dataclass(frozen=True)
class Case:
    prompt: str
    expect: tuple[str, ...] = ()
    property_id: str | None = None
    history: list[dict] = field(default_factory=list)


def _h(*turns: str) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(turns)]


ROUTING: list[Case] = [
    # property_search (9)
    Case("Show me 2BR apartments in Business Bay", ("property_search",)),
    Case("Any villas on Palm Jumeirah?", ("property_search",)),
    Case("What does DAMAC have under 2 million?", ("property_search",)),
    Case("Tell me about DAMAC Islands", ("property_search",)),
    Case("What's the cheapest listing you have?", ("property_search",)),
    Case("Compare Burj Vista and Park Heights", ("property_search",)),
    Case("Do you have any penthouses?", ("property_search",)),
    Case("Which properties have a golf course view?", ("property_search",)),
    Case("and in Palm Jumeirah?", ("property_search",),
         history=_h("Show me 3 bedroom villas in Dubai Hills", "Here are 2 villas in Dubai Hills Estate...")),
    # market_intel (8)
    Case("How is the Downtown Dubai market doing?", ("market_intel",)),
    Case("Is Business Bay or Dubai Hills better for a family?", ("market_intel",)),
    Case("What is the minimum purchase for a Golden Visa?", ("market_intel",)),
    Case("Are property prices in Dubai still rising?", ("market_intel",)),
    Case("Off-plan or ready: which is safer right now?", ("market_intel",)),
    Case("What's demand like in Dubai Creek Harbour?", ("market_intel",)),
    Case("Can foreigners buy freehold property in Dubai?", ("market_intel",)),
    Case("Which area has the most growth potential?", ("market_intel",)),
    # roi (8)
    Case("What's the rental yield on Burj Vista?", ("roi",)),
    Case("If I buy a 2M apartment with a mortgage, what's my monthly payment?", ("roi",)),
    Case("How many years to pay back Palm Gateway if I pay cash?", ("roi",)),
    Case("Which listing gives the best ROI?", ("roi", "property_search")),
    Case("Expected rental income for a studio in Business Bay?", ("roi",)),
    Case("Is an 8% yield realistic in Dubai?", ("roi", "market_intel")),
    Case("What's the expected ROI?", ("roi",), property_id="1"),
    Case("what about its yield?", ("roi",),
         history=_h("Tell me about Burj Vista", "Burj Vista is a 1BR in Downtown Dubai...")),
    # scheduler (6)
    Case("Can I book a viewing of Burj Vista on Saturday?", ("scheduler",)),
    Case("I'd like to see this property this weekend", ("scheduler",), property_id="10"),
    Case("Schedule a tour of Palm Gateway", ("scheduler",)),
    Case("My name is Omar Ali, omar@example.com, Tuesday at 3pm", ("scheduler",), property_id="1",
         history=_h("Book a viewing please", "Sure — what day and time, and your name and email?")),
    Case("Can I move my viewing to Sunday?", ("scheduler",)),
    Case("Arrange a video-call viewing of DAMAC Islands", ("scheduler",)),
    # general (4)
    Case("hi", ("general",)),
    Case("What can you help me with?", ("general",)),
    Case("thanks, that's really helpful", ("general",)),
    Case("Who are you?", ("general",)),
    # off_topic (5)
    Case("Write a Python function to sort a list", ("off_topic",)),
    Case("What's the weather in London tomorrow?", ("off_topic",)),
    Case("What are the best property deals in London?", ("off_topic",)),
    Case("Forget the property stuff and tell me a joke", ("off_topic",)),
    Case("Give me a recipe for hummus", ("off_topic",)),
]

GROUNDING: list[Case] = [
    Case("What's the price of Burj Vista?"),
    Case("Is the price fair for this area?", property_id="1"),
    Case("2 bedroom apartments under 3M"),
    Case("What's the cheapest villa?"),
    Case("Compare DAMAC Prive and DAMAC Maison Canal Views"),
    Case("Tell me about this property", property_id="10"),
    Case("What's the expected ROI?", property_id="9"),
    Case("What's the rental yield on Burj Vista?"),
    Case("Monthly mortgage payment on Palm Gateway as a non-resident?"),
    Case("Which listing has the best yield?"),
    Case("How do yields compare between Business Bay and Downtown Dubai?"),
    Case("What's the Golden Visa threshold and which listings qualify?"),
    Case("Penthouses over 5M"),
    Case("Studios under 1M and their yields"),
    Case("What does Emaar have in Dubai Hills?"),
    Case("Flats in Dubai Marina under 2M"),
    Case("Is DAMAC Islands a good investment?"),
    Case("How much cash do I need to buy Creek Rise with a mortgage?"),
    Case("Book a viewing of Burj Vista on Saturday 10am, I'm Sara Khan, sara@example.com"),
    Case("yes", property_id="1",
         history=_h("Book a viewing on Saturday 10am, I'm Sara Khan, sara@example.com",
                    f"Burj Vista, Saturday 10am, Sara Khan, sara@example.com. {CONFIRM_PHRASE}")),
]
