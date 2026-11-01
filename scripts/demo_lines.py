"""Narration lines for the demo video (key -> text). Used by demo_tts.py and record_demo.py."""
LINES = {
 "problem": "A one-person shop takes orders in chat, builds payment links by hand, checks who has paid, and answers refund requests at midnight.",
 "promise": "An AI agent can take that work, but it should never move money alone. So this one drafts, and the seller approves.",
 "app": "This is Solo Seller Order Agent on the real PayPal Sandbox. The agent is a real language model, Claude, and each reply is tagged AI model response.",
 "order": "The customer writes an order in plain language, with a shipping address.",
 "created": "Prices come from the shop catalog, never from the customer or the model. The agent calls the PayPal Agent Toolkit tool create order, and returns a checkout link.",
 "checkout": "The buyer follows the link to PayPal Checkout. This sandbox page appears in Korean, because of the test account's locale.",
 "login": "The buyer signs in with a PayPal sandbox account. The credentials are hidden in this recording.",
 "review": "The buyer reviews the payment and approves it.",
 "paid": "Back in the app, the payment is captured with the PayPal Orders API, and the order turns PAID.",
 "refund": "Later, the customer asks for a refund. The agent can only file a request.",
 "queue": "The request lands in the seller approval queue as PENDING. The order is still PAID, and the agent has no tool that can execute a refund.",
 "approve": "The seller reads the reason and the amount, and clicks Approve.",
 "done": "Only now does the app call the PayPal refund API. It comes back COMPLETED, the order is REFUNDED, and every step is in the audit log.",
 "arch": "Under the hood, the PayPal Agent Toolkit creates and reads orders. Capture and refund are REST calls behind a human-only endpoint, and the agent's tool list has no refund execution.",
}
