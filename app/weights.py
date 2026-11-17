"""Rebalance integer Keitaro shares while preserving pinned offers."""


def rebalance(offers):
    active = [offer for offer in offers if not offer.get("removed")]
    pinned = [offer for offer in active if offer.get("pinned")]
    free = [offer for offer in active if not offer.get("pinned")]
    for offer in offers:
        if offer.get("removed"):
            offer["share"] = 0
            offer["pinned"] = False
    pinned_total = sum(int(offer["share"]) for offer in pinned)
    if any(int(offer["share"]) < 0 or int(offer["share"]) > 100 for offer in pinned):
        raise ValueError("Pinned shares must be between 0 and 100.")
    if pinned_total > 100:
        raise ValueError("Pinned shares exceed 100%.")
    if active and not free and pinned_total != 100:
        raise ValueError("All shares are pinned, but their sum is not 100%.")
    if free:
        quotient, remainder = divmod(100 - pinned_total, len(free))
        for index, offer in enumerate(free):
            offer["share"] = quotient + (index < remainder)
    return offers
