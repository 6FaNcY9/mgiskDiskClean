# Track Specification: Production UI Redesign

## Overview
The goal of this track is to transform the existing Mrija Archive web application into a professional, production-ready email review tool. This involves implementing a modern three-panel layout, integrating VirusTotal (VT) for security scanning of attachments, and hardening the infrastructure for deployment.

## Goals
- **Modern UI**: A responsive, three-panel layout (Sidebar, Email List, Detail View) with a professional "space-grey" theme.
- **In-App Previews**: Ability to preview images and PDFs directly in the browser using an `inline=1` mode in the download endpoint.
- **Security Integration**: Automatically check attachments against VirusTotal and display status badges (clean, infected, pending) in the UI.
- **Infrastructure Hardening**: Secure the Docker Compose setup for production handoff, including environment variable management and port isolation.
- **Auditability**: Maintain deterministic behavior and ensure all decisions are correctly logged and exportable.

## Technical Requirements
- **PHP 8.3**: Use for all backend logic (no frameworks, no Composer).
- **MariaDB/MySQL**: Store VT cache and review decisions.
- **VirusTotal API v2**: Integrate for file scanning.
- **Vanilla JS & CSS**: Implement the UI components and theme system without external UI libraries.
- **Docker Compose**: Primary runtime environment.

## Key Components
1. **`download.php` Update**:
   - Support `?inline=1` for browser rendering.
   - Implement a VirusTotal gate: prevent downloads of infected files unless explicitly bypassed.
2. **Infrastructure Update**:
   - Update `docker-compose.yml` for production settings.
   - Configure environment variables for API keys and database credentials.
3. **`index.php` Rewrite**:
   - Implement the three-panel CSS/HTML structure.
   - Integrate the theme system (Dark/Light/Accent modes).
   - Display VT badges next to attachments.
   - Implement the AJAX-based review flow within the new layout.
4. **VT Service Integration**:
   - Utilize the existing `VtService` class to manage API calls and local caching.

## Acceptance Criteria
- [ ] Three-panel layout is fully functional and responsive.
- [ ] Users can switch between dark/light themes.
- [ ] Attachments can be previewed inline (Images, PDFs).
- [ ] VirusTotal status is displayed for all attachments.
- [ ] Infected attachments are blocked from download by default.
- [ ] All review decisions (keep/delete/unsure) are correctly saved to the database.
- [ ] Docker environment is hardened (no unnecessary open ports, secure secrets).
