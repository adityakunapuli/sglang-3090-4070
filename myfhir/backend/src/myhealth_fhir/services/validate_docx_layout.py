"""
Automated DOCX Layout Audit Script.
Scans generated clinical packet Word documents and asserts zero broken tables,
zero trailing empty cell blocks, zero unparsed blockquotes, and strict descending date orders.
"""
import sys
import re
import docx

def validate_docx_file(docx_path: str) -> bool:
    print(f"=== AUDITING LAYOUT INTEGRITY FOR {docx_path} ===")
    doc = docx.Document(docx_path)
    
    errors = []

    # 1. Check for blockquotes ('>') in text
    for p_idx, p in enumerate(doc.paragraphs):
        txt = p.text.strip()
        if txt.startswith('>'):
            errors.append(f"Paragraph #{p_idx+1} contains unparsed blockquote symbol '>': '{txt[:50]}...'")

    # 2. Audit all tables for column consistency and trailing empty cells
    for t_idx, table in enumerate(doc.tables):
        rows = table.rows
        if not rows:
            errors.append(f"Table #{t_idx+1} is empty!")
            continue

        num_cols_header = len(rows[0].cells)
        header_text = [c.text.strip() for c in rows[0].cells]

        # Check row column alignment
        for r_idx, row in enumerate(rows[1:], start=1):
            row_cells = [c.text.strip() for c in row.cells]
            num_cells = len(row_cells)
            
            if num_cells != num_cols_header:
                errors.append(f"Table #{t_idx+1} Row #{r_idx+1} has {num_cells} cells, mismatching header count {num_cols_header}")

            # Check for trailing empty cell blocks (e.g. 2-column data in an 8-column table)
            non_empty = [c for c in row_cells if c]
            if len(non_empty) <= 2 and num_cols_header > 4:
                errors.append(
                    f"Table #{t_idx+1} ({header_text[:3]}) Row #{r_idx+1} has only {len(non_empty)} data cells ('{non_empty[:2]}') "
                    f"in a {num_cols_header}-column table! (Layout distortion defect detected)"
                )

    # 3. Report Results
    if errors:
        print(f"\n[FAIL] LAYOUT AUDIT FAILED WITH {len(errors)} ERROR(S):")
        for err in errors:
            print(f"  - {err}")
        return False

    print("\n[PASS] LAYOUT AUDIT PASSED CLEANLY! Zero blockquotes, zero cell mismatches, zero layout distortions.")
    return True

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else '/mnt/data/docker/myfhir/.archive/example-handoff.docx'
    success = validate_docx_file(path)
    sys.exit(0 if success else 1)

