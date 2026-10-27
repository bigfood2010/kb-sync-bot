from dataclasses import dataclass, field

CHARS_PER_TOKEN = 3.5


@dataclass
class SyncPlan:
    added: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    deleted: list = field(default_factory=list)


def build_sync_plan(local_hashes, stored_hashes):
    plan = SyncPlan()
    for slug, local_hash in sorted(local_hashes.items()):
        if slug not in stored_hashes:
            plan.added.append(slug)
        elif stored_hashes[slug] != local_hash:
            plan.updated.append(slug)
        else:
            plan.skipped.append(slug)
    plan.deleted = sorted(set(stored_hashes) - set(local_hashes))
    return plan


def estimate_chunk_count(markdown, chunk_size, chunk_overlap):
    tokens = len(markdown) / CHARS_PER_TOKEN
    if tokens <= chunk_size:
        return 1
    return int(-(-(tokens - chunk_overlap) // (chunk_size - chunk_overlap)))
