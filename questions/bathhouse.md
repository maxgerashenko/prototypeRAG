# Bathhouse (abathhouse.com)

Default location: Williamsburg. Other locations: Flatiron, Atlantic Ave, Philadelphia.

## Basics

| Ask | Expected |
|---|---|
| What's your address? | 103 North 10th Street, Brooklyn, NY 11249 |
| What's your phone number? | (929) 489-2284 |
| What's your email? | williamsburg@abathhouse.com |
| What time do you open? / What are your hours on Sunday? | 7 days a week, 8am to 11:30pm. Known gap: hours are only in the site footer and may not be found yet (V23, until `facts.py`) |
| Are you open right now? | Should use the current time in New York, not just say yes or no (V22) |
| What locations do you have? | Williamsburg, Flatiron, Atlantic Ave, Philadelphia |

## Other locations

| Ask | Expected |
|---|---|
| What's the address of the Flatiron location? | 14 West 22nd Street, New York, NY 10010 |
| What's the phone number in Philadelphia? | (267) 802-2560, 1418 Walnut Street |
| Where is Atlantic Ave? | 540 Atlantic Avenue, Brooklyn, NY 11217, (929) 822-5929 |

## Prices and services

| Ask | Expected |
|---|---|
| How much is a day pass? | Starts at $39, varies by day and time |
| How much is a massage? How long is it? | From $154; Full-Body 50 min, Pro 80 min, Prenatal 50 min, Couples 50 or 80 min |
| Do you do body scrubs? | Hammam scrubs from $115, 30 or 50 min, couples option |
| Does a massage include the saunas? | Yes, treatments include amenity access |
| Do you offer memberships? | Yes (details from the /memberships page) |
| Do you sell gift cards? | Yes (Gift Cards page) |
| Is it good for first timers? | Should point to the First Timers info |

## Amenities

| Ask | Expected |
|---|---|
| What saunas do you have? | Banya (185–195°F), Dry Sauna (175–190°F), Tropical Sauna (up to 185°F), Event Sauna (up to 175°F) |
| How hot is the banya? | 185°F to 195°F, heated by basalt stones |
| How many pools are there? | Eight: three hot (104°F), two cold plunges (45°F, 50°F), two neutral (98°F), Rooftop Pool (84°F) |
| How cold is the cold plunge? | 45°F and 50°F |
| Do you have a steam room? | Starlight Steam Room, 115°F |
| Do you have a rooftop pool? Is it open in winter? | Yes, open in winter as long as weather permits |
| Can I get a drink by the pool? | Rooftop Pool Bar: cocktails, Prosecco on tap, local beers, snacks |
| Do you have guided sauna sessions? | Yes, complimentary, in the Event Sauna |

## Should not know (expect "I don't know" or an offer to help another way)

| Ask | Why |
|---|---|
| Do you offer skydiving lessons? | Not on the site |
| Why did the Williamsburg sauna get bigger in 2024? | Blog history, must not be stated as a current fact |
| What's the manager's name? | Not on the site; must not guess |

## Conversation and voice behaviour

| Try | Expected |
|---|---|
| Interrupt while it's talking | It stops and listens |
| Ask a follow-up ("and how much is that?") | Uses the earlier turn for context |
| Stay silent for a while | Re-prompt after ~6 s, not built yet (V17) |
| "Thanks, bye" | Says goodbye and ends the call, not built yet (V17) |
| "Can I book a massage for Saturday at 3?" | No booking tools yet (step 5); should not pretend it booked |
| "Can I talk to a person?" | Transfer / take a message, not built yet |
