# The Python tests

The other half of the repository: the workflows that talk to AnkiWeb and to
Claude. These cover the parts that are arithmetic and string handling — no
Anki, no network, no key needed — which is where the bugs that reached the app
actually were.

```
pip install pytest
python3 -m pytest test/python -q
```

`test_build_deck.py` covers the conversion from an Anki export into the deck
the app reviews: the stability floor FSRS refuses to go under, the ease-to-
difficulty map, and the far-future due date Anki writes for a card it will
never show again — which is a tombstone, not a schedule, and which is now
counted and reported rather than quietly replaced with today.

`test_keys_and_contract.py` covers the messages that made "it doesn't work"
take hours: an empty key, the wrong kind of token, and the masked key people
paste out of the console, which is the right length and the right prefix and
is not a key. It also holds the explanation contract to the shape the browser
needs, including that it contains no nullable unions — the shape known to work
with this API has none.
