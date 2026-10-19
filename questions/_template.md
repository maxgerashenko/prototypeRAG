# Any new business (template)

Copy this file to `<business-name>.md` for each business you crawl and fill in the
expected column from its site.

| Ask | Checks |
|---|---|
| What's your address / phone / email? | Contact facts from the page or footer |
| What are your hours? Are you open now? | Hours from site chrome; local time zone |
| What do you offer? How much is X? | Services and prices |
| Where are your other locations? | Location detection (DEC-37) |
| Something clearly not on the site | Refuses instead of inventing |
| Something only in a blog post | Not stated as a current fact |
| A custom reply you added via `/businesses/{id}/custom-replies` | Custom reply wins over page text |
| "Bye" | Polite end of call (once V17 is built) |
