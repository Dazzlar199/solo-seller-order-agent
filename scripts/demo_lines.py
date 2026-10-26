"""Narration lines for the demo video (key -> text). Used by demo_tts.py and record_demo.py."""
LINES = {
 "problem": "A one-person shop takes orders in chat. Then the seller retypes every item into a payment link, checks by hand whether it was paid, and handles refund requests at midnight.",
 "promise": "An AI agent can do that work, but nobody wants an agent that moves money on its own. So this one drafts, and the seller approves.",
 "app": "This is Solo Seller Order Agent, running against the real PayPal Sandbox. The badge says PayPal Sandbox. No language model key is set, so the built-in rule-based parser handles the chat.",
 "order": "The customer writes an order in plain language and gives a shipping address.",
 "created": "Prices come from the shop catalog, never from the customer or the model. The agent calls the PayPal Agent Toolkit tool create order, and returns a checkout link.",
 "checkout": "The buyer follows the link to PayPal Checkout. This sandbox page appears in Korean, because of the test account's locale.",
 "login": "The buyer signs in with a PayPal sandbox account. The credentials are hidden in this recording.",
 "review": "The buyer reviews the payment and approves it.",
 "paid": "Back in the app, the payment is captured with the PayPal Orders API, and the order turns PAID.",
 "refund": "Later, the customer asks for a refund. The agent can only file a request.",
 "queue": "The request lands in the seller approval queue as PENDING, and the order is still PAID. The agent has no tool that can execute a refund.",
 "approve": "The seller reads the reason and the amount, and clicks Approve.",
 "done": "Only now does the app call the PayPal Payments API. The refund comes back COMPLETED, and the order is REFUNDED. Every step is in the audit log.",
 "arch": "Under the hood, the agent uses the PayPal Agent Toolkit for create order and get order details. Capture and refund are REST calls behind a human-only endpoint. The agent's tool list contains no refund execution. The code is open source on GitHub.",
}
