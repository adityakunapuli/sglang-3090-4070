#!/usr/bin/env python3
"""
Transfer homepage labels from docker-compose files to config files.

This script:
1. Extracts homepage labels from docker-compose files
2. Comments out labels in original files
3. Appends labels as new entries to services.yaml with appropriate structure
"""

import os
import re

ROOT_DIR = '/mnt/data/docker'
CONFIG_PATH = os.path.join(ROOT_DIR, 'homepage/config/services.yaml')
EXCLUDE_DIRS = ['.git', '.archive', '.lib', 'db', 'data', 'logs', 'config', 'public', 'build', 'node_modules']

def extract_homepage_labels_from_file(file_path):
    """Extract homepage labels from a docker-compose file."""
    labels = []
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Find all homepage labels - handle quoted and unquoted patterns
        pattern = r'["\s]*homepage\.(\w+(?:\.\w+)*)\s*=\s*["\']?(.*?)["\']?\s*["\s]*'
        matches = re.findall(pattern, content)
        
        for key, value in matches:
            if key.startswith('widget.'):
                # Widget keys need to be normalized
                clean_key = key.replace('widget.', '')
                # Remove trailing quotes from value
                value = value.rstrip('"')
                labels.append(f'homepage.widget.{clean_key}={value}')
            else:
                labels.append(f'homepage.{key}={value}')
    except Exception as e:
        print(f"  Error reading {file_path}: {e}")
    
    return labels

def comment_out_labels_in_file(file_path, labels):
    """Comment out homepage labels in a docker-compose file."""
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
        
        updated_lines = []
        in_labels_section = False
        
        for line in lines:
            stripped = line.strip()
            
            # Check if we're in a labels section
            if 'labels:' in stripped and not stripped.startswith('#'):
                in_labels_section = True
                updated_lines.append(line)
                continue
            
            # Check if we're leaving a labels section
            if in_labels_section and stripped and not stripped.startswith('- ') and not stripped.startswith('#'):
                in_labels_section = False
            
            # Comment out homepage labels
            if in_labels_section and '- ' in stripped and any(f'homepage.' in label for label in labels):
                for label in labels:
                    # Convert label to pattern (handle both quoted and unquoted)
                    pattern1 = f'"{label}"'
                    pattern2 = f'- "{label}"'
                    pattern3 = f"- '{label}'"
                    
                    if pattern1 in line or pattern2 in line or pattern3 in line:
                        # Check if already commented
                        if stripped.startswith('#'):
                            updated_lines.append(line)
                        else:
                            # Comment out the line
                            updated_lines.append(line.replace(stripped, f'# {stripped}'))
                        break
                else:
                    updated_lines.append(line)
            else:
                updated_lines.append(line)
        
        # Write back
        with open(file_path, 'w') as f:
            f.writelines(updated_lines)
        
        return True
    except Exception as e:
        print(f"  Error processing {file_path}: {e}")
        return False

def convert_label_to_config_format(label):
    """Convert a homepage label to config format."""
    # Handle both formats: "homepage.key=value" or "homepage.widget.key=value"
    if '=' not in label:
        return None
    
    key, value = label.split('=', 1)
    
    # Remove quotes from value
    value = value.strip('"')
    
    # Determine target structure
    if 'widget.' in key:
        # Widget configuration - might need to add nested structure
        widget_key = key.replace('homepage.widget.', '')
        return {
            'widget': {
                widget_key: value
            }
        }
    else:
        # Regular label - determine which field it is
        if key == 'homepage.group':
            return {'group': value}
        elif key == 'homepage.name':
            return {'name': value}
        elif key == 'homepage.icon':
            return {'icon': value}
        elif key == 'homepage.href':
            return {'href': value}
        elif key == 'homepage.description':
            return {'description': value}
        elif key == 'homepage.target':
            return {'target': value}
        else:
            # Unknown label - try to add as-is
            return {key.replace('homepage.', ''): value}

def transfer_labels_to_config(labels_by_file):
    """Transfer labels to config file, creating new entries for missing services."""
    # First, read the existing config
    with open(CONFIG_PATH, 'r') as f:
        config_text = f.read()
    
    # We'll append new services to the config
    # Find the end of the config file (before commented sections)
    lines = config_text.split('\n')
    
    # Find where to insert new services
    insert_index = 0
    for i, line in enumerate(lines):
        # Look for commented service definitions to insert after them
        if line.strip().startswith('# - ') or line.strip().startswith('# - ') and i > 0:
            # Found a commented service section, insert before it
            for j in range(i-1, -1, -1):
                if lines[j].strip().endswith(':'):
                    insert_index = j + 1
                    break
            break
    
    # Build new service entries
    new_entries = []
    for file_path, labels in labels_by_file.items():
        # Extract service name from path
        service_name = os.path.basename(os.path.dirname(file_path))
        
        # Skip if it's already in the config (by looking for commented entries)
        existing_commented = False
        for line in lines:
            if f'# - {service_name}' in line or f'#     - {service_name}' in line:
                existing_commented = True
                break
        
        if existing_commented:
            continue
        
        # Create new service entry
        if labels:
            # Determine service category from labels
            service_data = {}
            
            # Extract key properties
            for label in labels:
                config_item = convert_label_to_config_format(label)
                if config_item:
                    for key, value in config_item.items():
                        if key == 'widget':
                            if 'widget' not in service_data:
                                service_data['widget'] = {}
                            service_data['widget'].update(value)
                        else:
                            service_data[key] = value
            
            # Skip if we couldn't determine the service structure
            if not service_data:
                continue
            
            # Convert to YAML format
            yaml_lines = []
            # Add service name
            yaml_lines.append(f'#    - {service_name}:')
            
            # Add properties
            if 'name' in service_data:
                yaml_lines.append(f'#        name: {service_data["name"]}')
            elif 'href' in service_data:
                # Extract service name from href or use directory name
                name = service_name.capitalize()
                yaml_lines.append(f'#        name: {name}')
            
            if 'icon' in service_data:
                yaml_lines.append(f'#        icon: {service_data["icon"]}')
            
            if 'href' in service_data:
                yaml_lines.append(f'#        href: {service_data["href"]}')
            
            if 'description' in service_data:
                yaml_lines.append(f'#        description: {service_data["description"]}')
            
            if 'target' in service_data:
                yaml_lines.append(f'#        target: {service_data["target"]}')
            
            # Add widget configuration
            if 'widget' in service_data:
                yaml_lines.append(f'#        widget:')
                for widget_key, widget_value in service_data['widget'].items():
                    yaml_lines.append(f'#          {widget_key}: {widget_value}')
            
            # Add empty line between services
            yaml_lines.append('')
            new_entries.append('\n'.join(yaml_lines))
    
    # Insert new entries after commented services section
    if insert_index > 0:
        lines.insert(insert_index, '\n'.join(new_entries))
    else:
        # Append at the end of the file before the generic comments
        yaml_lines = []
        for i, line in enumerate(lines):
            if line.strip() == '#    - Portainer:':
                yaml_lines.extend(lines[:i])
                break
        yaml_lines.extend(new_entries)
        lines = yaml_lines
    
    # Write updated config
    with open(CONFIG_PATH, 'w') as f:
        for line in lines:
            f.write(str(line))
    
    return True

def main():
    print("=== HOMEPAGE LABELS TRANSFER ===\n")
    
    # Find all docker-compose files
    compose_files = []
    
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        for file in files:
            if 'docker-compose' in file and (file.endswith('.yml') or file.endswith('.yaml')):
                full_path = os.path.join(root, file)
                compose_files.append(full_path)
    
    print(f"Found {len(compose_files)} docker-compose files")
    
    # Collect labels
    all_labels_by_file = {}
    
    for file_path in compose_files:
        labels = extract_homepage_labels_from_file(file_path)
        
        if labels:
            all_labels_by_file[file_path] = labels
            print(f"  {os.path.basename(file_path)}: Found {len(labels)} homepage labels")
    
    total_labels = sum(len(labels) for labels in all_labels_by_file.values())
    print(f"\nTotal homepage labels: {total_labels}")
    
    if total_labels == 0:
        print("No homepage labels found.")
        return
    
    # Comment out labels in original files
    print("\n=== Commenting out labels in original files ===")
    processed_files = 0
    for file_path, labels in all_labels_by_file.items():
        if comment_out_labels_in_file(file_path, labels):
            processed_files += 1
            print(f"  {os.path.basename(file_path)}: Commented out {len(labels)} labels")
    
    print(f"\nProcessed {processed_files} files")
    
    # Transfer to config file
    print("\n=== Transferring to config file ===")
    if transfer_labels_to_config(all_labels_by_file):
        print("  Config updated successfully")
    
    print("\n=== Transfer complete ===")
    print(f"Commented out {total_labels} labels in {processed_files} files")
    print(f"Labels available in: {CONFIG_PATH}")

if __name__ == '__main__':
    main()
