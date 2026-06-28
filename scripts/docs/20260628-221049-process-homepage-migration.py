#!/usr/bin/env python3
"""
Migrate homepage labels from docker-compose files to config files.

This script:
1. Reads all docker-compose files for homepage labels
2. Comments out labels in original files
3. Converts them to config format in services.yaml
4. Creates new service entries where needed
"""

import os
import re
import yaml
import sys

# Configuration
ROOT_DIR = '/mnt/data/docker'
CONFIG_PATH = os.path.join(ROOT_DIR, 'homepage/config/services.yaml')
EXCLUDE_DIRS = ['.git', '.archive', '.lib', 'db', 'data', 'logs', 'config', 'public', 'build', 'node_modules']

def extract_homepage_labels(content):
    """Extract homepage labels from docker-compose content."""
    labels = []
    lines = content.split('\n')
    in_labels_section = False
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if 'labels:' in stripped and not stripped.startswith('#'):
            in_labels_section = True
            continue
        
        if in_labels_section and stripped.startswith('- '):
            label = stripped[2:]
            if label.startswith('"homepage.'):
                labels.append(label.strip('"'))
        elif in_labels_section and not stripped.startswith('- ') and stripped and not stripped.startswith('#'):
            break
    
    return labels

def comment_out_labels(file_path, labels):
    """Comment out homepage labels in a docker-compose file."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    in_labels_section = False
    new_lines = []
    
    for line in lines:
        stripped = line.strip()
        if 'labels:' in stripped and not stripped.startswith('#'):
            in_labels_section = True
            new_lines.append(line)
            continue
        
        if in_labels_section and stripped.startswith('- '):
            current_label = stripped[2:]
            if any(homepage_label in current_label for homepage_label in labels):
                if not stripped.startswith('#'):
                    new_lines.append('        # - "' + current_label.strip('"') + '"\n')
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        elif in_labels_section and not stripped.startswith('- ') and stripped and not stripped.startswith('#'):
            in_labels_section = False
            new_lines.append(line)
        else:
            new_lines.append(line)
    
    with open(file_path, 'w') as f:
        f.writelines(new_lines)
    
    return True

def parse_label_to_config(label):
    """Convert a homepage label to config dictionary."""
    # Remove "homepage." prefix
    content = label.replace('homepage.', '')
    
    # Parse key-value pairs
    config = {}
    
    for part in content.split(' '):
        if '=' in part:
            key, value = part.split('=', 1)
            if key == 'widget.key':
                # Handle widget.key specially (contains ${} variable)
                config[key] = f'{{{{{value.strip('{}')}}}'
            elif key in ['href', 'icon', 'name', 'group', 'target', 'url', 'description', 'type', 'version', 'fields', 'showStats', 'env']:
                config[key] = value
    
    # Special handling for widget-specific keys
    for label in labels:
        if 'widget.' in label:
            widget_part = label.split('widget.')[1]
            widget_key = widget_part.split('=')[0]
            widget_value = widget_part.split('=')[1] if '=' in widget_part else ''
            
            # Remove trailing quote if present
            if widget_value.endswith('"'):
                widget_value = widget_value[:-1]
            
            config[widget_key] = widget_value
    
    return config

def update_config_file(labels_by_file):
    """Update the services.yaml config file with homepage labels."""
    with open(CONFIG_PATH, 'r') as f:
        content = f.read()
    
    # Parse existing YAML
    config_data = yaml.safe_load(content)
    
    # Process each file and add to config
    for file_path, labels in labels_by_file.items():
        # Extract service name from path
        # Example: ./paperless-ngx/docker-compose.yml -> paperless-ngx
        service_name = os.path.basename(os.path.dirname(file_path))
        
        # Skip if service doesn't exist in current config
        if service_name not in config_data:
            print(f"Warning: Service '{service_name}' not found in config. Skipping...")
            continue
        
        # Get the service config
        service_config = config_data[service_name]
        
        # Update existing service config with labels
        for label in labels:
            # Convert label to config format
            config = parse_label_to_config(label)
            
            # Add config to service
            if 'widget' not in service_config:
                service_config['widget'] = {}
            
            # Add each key to widget if it contains widget., otherwise to top level
            for key, value in config.items():
                if 'widget.' in key:
                    clean_key = key.replace('widget.', '')
                    service_config['widget'][clean_key] = value
                else:
                    # Handle special fields
                    if key in ['target']:
                        if key not in service_config:
                            service_config[key] = value
                    elif key in ['icon', 'href', 'name', 'group', 'description']:
                        if key not in service_config:
                            service_config[key] = value
                    elif key == 'showStats':
                        service_config[key] = value.lower() == 'true'
    
    # Write updated YAML back to file
    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
    
    return True

def main():
    """Main migration function."""
    print("=== HOMEPAGE LABELS MIGRATION ===\n")
    
    # Find all docker-compose files
    compose_files = []
    
    for root, dirs, files in os.walk(ROOT_DIR):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        for file in files:
            if 'docker-compose' in file and (file.endswith('.yml') or file.endswith('.yaml')):
                full_path = os.path.join(root, file)
                compose_files.append(full_path)
    
    print(f"Found {len(compose_files)} docker-compose files")
    
    # Collect all homepage labels
    all_labels_by_file = {}
    
    for file_path in compose_files:
        with open(file_path, 'r') as f:
            content = f.read()
        
        labels = extract_homepage_labels(content)
        
        if labels:
            all_labels_by_file[file_path] = labels
            print(f"  {file_path}: Found {len(labels)} homepage labels")
    
    total_labels = sum(len(labels) for labels in all_labels_by_file.values())
    print(f"\nTotal homepage labels found: {total_labels}")
    
    if total_labels == 0:
        print("No homepage labels found. Nothing to migrate.")
        return
    
    # Comment out labels in original files
    print("\n=== Commenting out labels in original files ===")
    for file_path, labels in all_labels_by_file.items():
        print(f"  Processing {file_path}")
        comment_out_labels(file_path, labels)
    
    # Update config file
    print("\n=== Updating config file ===")
    print(f"  Updating {CONFIG_PATH}")
    
    if update_config_file(all_labels_by_file):
        print("  Config updated successfully")
    
    print("\n=== Migration complete ===")
    print(f"Processed {len(all_labels_by_file)} files with {total_labels} labels")
    print(f"Labels moved to: {CONFIG_PATH}")

if __name__ == '__main__':
    main()
