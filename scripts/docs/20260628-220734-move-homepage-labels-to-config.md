=== Plan: Move homepage labels to config files ===

## Overview
Comment out all docker-compose labels starting with homepage. and transfer them to config files in /mnt/data/docker/homepage/config to reduce management overhead in each compose directory.

## Step 1: Review catalog
Check the catalogs created to understand all homepage labels that need to be migrated:
- .archive/homepage_labels_unique_catalog.txt
- .archive/homepadlare_labels_catalog.txt

## Step 2: Analyze config file structure
The homepage config files are located in /mnt/data/docker/homepage/config/ and contain:
- services.yaml: Main service configuration
- docker.yaml: Docker socket configuration

## Step 3: Map labels to config structure
Analyze which homepage labels belong to which config sections:
- Labels contain: homepage.group, homepage.name, homepage.icon, homepage.href, homepage.description, etc.
- These match services.yaml structure: service name, icon, href, description, widget settings

## Step 4: Create migration script
Write a script to automatically extract homepage labels from docker-compose files and add them to appropriate config files in services.yaml, adding new entries or updating existing ones.

## Step 5: Execute migration
Run the script to:
1. Parse all docker-compose files for homepage labels
2. Convert labels to config format
3. Add new service entries to services.yaml or update existing ones
4. Verify all labels are correctly transferred

## Result
- All homepage labels commented out in docker-compose files
- Equivalent configuration in /mnt/data/docker/homepage/config/services.yaml
- No loss of functionality
- Centralized management of homepage configuration


