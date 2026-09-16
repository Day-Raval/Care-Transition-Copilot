"""
Splits discharge note text into chunks suitable for embedding.

SECTION-AWARE, not whole-note: confirmed via scripts/check_chf_notes.py
that whole-note embedding dilutes background/comorbidity mentions (e.g.
a CHF mention buried in a "Plan" section's long procedure/lab list) when
they aren't the encounter's main focus — a query for "congestive heart
failure" failed to surface notes that genuinely mention it, because the
mention was a few words out of a 300+ word note mostly about something
else. Splitting by the note's own markdown headers (Chief Complaint,
History of Present Illness, Social History, Medications, Assessment and
Plan, etc.) gives each section its own embedding, so a CHF mention in the
Plan section is no longer drowned out by an unrelated Chief Complaint.

Preamble sections (the bare date line before the first header) are
dropped entirely — confirmed they never contain anything beyond the date,
which is already captured in every chunk's discharge_ts metadata.

Falls back to the old whole-note word-count chunking for any section that
doesn't fit the expected header structure, or if a single section is
itself too long (rare, but a verbose History of Present Illness for a
complex chronic patient could still exceed one chunk).
"""

import re

CHUNK_SIZE_WORDS = 375  # ~500 tokens at ~0.75 words/token
CHUNK_OVERLAP_WORDS = 40  # ~50 tokens — avoids splitting a sentence's
                           # meaning across a chunk boundary

SECTION_HEADER_PATTERN = re.compile(r"\n(?=#{1,3}\s)")
HEADER_TEXT_PATTERN = re.compile(r"^#{1,3}\s*(.+)$")


def split_into_sections(text: str) -> list[tuple[str, str]]:
    """
    Returns [(section_name, section_text), ...]. Any content before the
    first header (typically just the note's date line) is kept as a
    'Preamble' section rather than discarded here — chunk_note_record()
    is what drops it, so split_into_sections() itself stays reusable if
    someone later wants to inspect that content.
    """
    if not text or not text.strip():
        return []

    pieces = SECTION_HEADER_PATTERN.split(text.strip())
    sections = []

    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue

        first_line, _, rest = piece.partition("\n")
        header_match = HEADER_TEXT_PATTERN.match(first_line)
        if header_match:
            section_name = header_match.group(1).strip()
            section_text = rest.strip()
        else:
            # No header on this piece — either the preamble (date line)
            # before the first real header, or a note that doesn't follow
            # the expected template at all.
            section_name = "Preamble"
            section_text = piece

        if section_text:
            sections.append((section_name, section_text))

    return sections


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE_WORDS, overlap: int = CHUNK_OVERLAP_WORDS) -> list[str]:
    """
    Splits on whitespace into overlapping word-count windows. Used as a
    fallback WITHIN a section if that section alone is too long for one
    chunk — most sections (Chief Complaint, Social History, Allergies)
    never hit this path; History of Present Illness or Plan occasionally
    might for complex chronic patients.
    """
    words = text.split()
    if len(words) <= chunk_size:
        return [text.strip()] if text.strip() else []

    chunks = []
    start = 0
    step = chunk_size - overlap
    while start < len(words):
        chunk_words = words[start:start + chunk_size]
        chunks.append(" ".join(chunk_words))
        if start + chunk_size >= len(words):
            break
        start += step
    return chunks


MIN_CHUNK_WORDS = 8  # below this, a chunk carries too little distinctive
                       # content to embed as its own searchable unit —
                       # confirmed via real query results: 3-4 word
                       # boilerplate chunks ("No Active Medications")
                       # were winning against genuinely relevant longer
                       # chunks across multiple unrelated queries

def chunk_note_record(record, chunk_size=CHUNK_SIZE_WORDS, overlap=CHUNK_OVERLAP_WORDS):
    text = record.get("note_text") or ""
    sections = split_into_sections(text)
    sections = [(name, txt) for name, txt in sections if name != "Preamble"]

    if not sections:
        return []

    # Merge trivially short sections into the next section rather than
    # embedding them alone — e.g. "Allergies: No Known Allergies" (3
    # words) gets folded into whatever follows it.
    merged_sections = []
    pending_prefix = ""
    for section_name, section_text in sections:
        if len(section_text.split()) < MIN_CHUNK_WORDS:
            pending_prefix += f"{section_name}: {section_text}. "
            continue
        if pending_prefix:
            section_text = pending_prefix + section_text
            pending_prefix = ""
        merged_sections.append((section_name, section_text))
    if pending_prefix and merged_sections:
        # trailing short section(s) with nothing after them — attach to the last real section
        last_name, last_text = merged_sections[-1]
        merged_sections[-1] = (last_name, last_text + " " + pending_prefix)
    elif pending_prefix:
        # the ENTIRE note was short sections — keep it rather than losing the note
        merged_sections = [("Summary", pending_prefix.strip())]

    chunks = []
    for section_name, section_text in merged_sections:
        sub_pieces = chunk_text(section_text, chunk_size, overlap)
        for sub_piece in sub_pieces:
            chunks.append({"section_name": section_name, "text": f"{section_name}: {sub_piece}"})

    return [
        {
            "chunk_id": f"{record['encounter_id']}_chunk{i}",
            "text": c["text"], "section_name": c["section_name"],
            "patient_id": record["patient_id"], "encounter_id": record["encounter_id"],
            "discharge_ts": record["discharge_ts"], "chunk_index": i, "total_chunks": len(chunks),
        }
        for i, c in enumerate(chunks)
    ]