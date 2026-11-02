# Bathhouse (abathhouse.com)

Default location: Williamsburg. Other locations: Flatiron, Atlantic Ave, Philadelphia (and Berkeley Heights, NJ, which has facts but no location row yet).

Expected = the key fact only (checked against the DB facts, 2026-11-02), not the full sentence.

## Basics

| Ask | Expected |
|---|---|
| What's your address? | 103 North 10th Street, Brooklyn, NY 11249 |
| What's your phone number? | (929) 489-2284 |
| What's your email? | williamsburg@abathhouse.com |
| What time do you open? / What are your hours on Sunday or Saturday? | Daily, 8am to 11:30pm |
| Are you open right now? | Should use the current time in New York, not just say yes or no (V22) |
| What locations do you have? | Williamsburg, Flatiron, Atlantic Ave, Philadelphia (+ Berkeley Heights) |

## Other locations

| Ask | Expected |
|---|---|
| What's the address of the Flatiron location? | 14 West 22nd Street, New York, NY 10010 |
| What's the Flatiron phone number? | (646) 891-1315 |
| What's the phone number in Philadelphia? | (267) 802-2560, not the Williamsburg number |
| Where is Atlantic Ave? | 540 Atlantic Avenue, Brooklyn, NY 11217, (929) 822-5929 |

## Prices and services

| Ask | Expected |
|---|---|
| How much is a day pass? | Starts at $39, varies by day and time |
| How much is a massage? How long is it? | From $154; Full-Body $204 (50 min), Pro $308 (80 min), Couples 50 or 80 min |
| Do you do body scrubs? | From $115, 30 or 50 min, couples option |
| Does a massage include the saunas? | Yes, treatments include amenity access |
| Do you offer memberships? | Yes: Starter $145, Standard $225 |
| Do you sell gift cards? | Yes, digital and physical |
| Is it good for first timers? | Should point to the What to Expect page |

## Amenities

| Ask | Expected |
|---|---|
| What saunas do you have? | Banya, Dry Sauna, Tropical Sauna, Event Sauna |
| How hot is the banya? | 185°F to 195°F, heated by basalt stones |
| How many pools are there? | Hot, neutral, cold plunges and the rooftop pool; no count in the facts |
| How cold is the cold plunge? | 45°F and 50°F |
| Do you have a steam room? | Starlight Steam Room, 115°F |
| Do you have a rooftop pool? Is it open in winter? | Yes, open in winter as long as weather permits |
| Can I get a drink by the pool? | Yes, Rooftop Pool Bar: beer, natural wine, Prosecco, snacks |
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
| Call and wait for it to connect | About 1 s of silence, then the greeting starts (`VOICE_GREETING_DELAY_S`, 0 = immediately) |
| Interrupt while it's talking | It stops and listens |
| Ask a follow-up ("and how much is that?") | Uses the earlier turn for context |
| Stay silent for a while | Re-prompt after ~6 s, not built yet (V17) |
| "Thanks, bye" | Says goodbye and ends the call, not built yet (V17) |
| "Can I book a massage for Saturday at 3?" | No booking tools yet (step 5); should not pretend it booked |
| "Can I talk to a person?" | Transfer / take a message, not built yet |
