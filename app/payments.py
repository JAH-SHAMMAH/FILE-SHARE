# Minimal payments stub for compatibility

def process_payment(*args, **kwargs):
    """Stub for payment processing. Replace with real implementation if needed."""
    return {"status": "success", "message": "Payment processed (stub)"}

def create_order(*args, **kwargs):
    """Stub for create_order."""
    return {"status": "created"}

def capture_order(*args, **kwargs):
    """Stub for capture_order."""
    return {"status": "captured"}

def get_access_token(*args, **kwargs):
    """Stub for get_access_token."""
    return "access_token_stub"

def verify_webhook_signature(*args, **kwargs):
    """Stub for verify_webhook_signature."""
    return True

def paystack_initialize_transaction(*args, **kwargs):
    """Stub for paystack_initialize_transaction."""
    return {"status": "initialized"}

def paystack_verify_transaction(*args, **kwargs):
    """Stub for paystack_verify_transaction."""
    return {"status": "verified"}

# Add any other stubs needed for imports from .payments
