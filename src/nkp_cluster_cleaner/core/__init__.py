"""
Domain model and decision logic.

Nothing in here talks to Kubernetes, Redis or the network. `criteria.evaluate`
is the heart of the tool and is deliberately a pure function of its inputs.
"""
