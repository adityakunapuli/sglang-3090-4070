#!/usr/bin/env python3
"""
Fix issue labels that have malformed prefixes (like homepage.1.*) to correct homepage.*
"""

import os

ROOT_DIR = '/mnt/data/docker'
FILES_TO_CHECK = [
    'frigate/docker-compose.yml',
]

def fix_label_prefix_in_file(file_path):
    """Fix labels that have numeric prefixes like homepage.1. to homepage."""
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Fix numeric prefixes
        fixed_content = content.replace('homepage.1.', 'homepage.')
        
        # Also fix multiple numeric prefixes if present
        fixed_content = fixed_content.replace('homepage.2.', 'homepage.')
        
        # Write back if changes were made
        if fixed_content != content:
            with open(file_path, 'w') as f:
                f.write(fixed_content)
            print(f"  Fixed labels in {file_path}")
            return True
        else:
            print(f"  No changes needed in {file_path}")
            return False
    except Exception as e:
        print(f"  Error processing {file_path}: {e}")
        return False

def main():
    print("=== FIXING ISSUE LABELS ===\n")
    
    total_fixed = 0
    
    for file_path in FILES_TO_CHECK:
        full_path = os.path.join(ROOT_DIR, file_path)
        print(f"Checking {file_path}")
        
        if os.path.exists(full_path):
            if fix_label_prefix_in_file(full_path):
                total_fixed += 1
        else:
            print(f"  File not found: {file_path}")
    
    print(f"\n=== Fix complete ===")
    print(f"Fixed {total_fixed} files")
    
    # Verify the fixes
    print("\n=== Verification ===")
    for file_path in FILES_TO_CHECK:
        full_path = os.path.join(ROOT_DIR, file_path)
        if os.path.exists(full_path):
            remaining_bad = 0
            with open(full_path, 'r') as f:
                for line in f:
                    if 'homepage.1.' in line or 'homepage.2.' in line:
                        remaining_bad += 1
            
            if remaining_bad > 0:
                print(f"WARNING: {file_path} still has {remaining_bad} bad labels")
            else:
                print(f"✓ {file_path} verified - no bad labels found")

if __name__ == '__main__':
    main()
