# Media Management v1.7.0

A web-based application for managing your physical media collection (CDs, Vinyl, Books, Movies, Video Games).
The system offers integrations with Discogs, Spotify, and Google Books to automatically load metadata and covers.

## Features

*   **Inventory**: Capture media with barcode scanner support or manual entry.
*   **Automatic Metadata**:
    *   **Music**: Search via Discogs API (Cover, Tracklists, Year).
    *   **Books**: Search via Google Books, OpenLibrary, and Amazon (Cover, Author, Description).
*   **Spotify Integration**: Preview player for CDs and Vinyls directly in the detail view.
*   **Location Management**: Hierarchical storage locations (e.g., Living Room > Shelf A > Compartment 1).
*   **Lending Status**: Mark lent items including PDF export and print view.
*   **QR Codes**: Generation of QR codes for each item for quick scanning.
*   **Backup & Restore**: Complete backup of the database and images as a ZIP file.
*   **User Management**: Role-based access (Admin/User).

## Installation with Docker (Recommended)

The application is optimized for operation with Docker Compose.

### 1. Preparation

Ensure that Docker and Docker Compose are installed.
Create a `docker-compose.yml` (or use the enclosed one) and start the container.

### 2. Configuration (Environment Variables)

Configuration is done via environment variables in the `docker-compose.yml` or a `.env` file.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `APP_PORT` | `5000` | The port on which the web interface is accessible (Host Port). |
| `UPLOAD_PATH` | `./data/uploads` | Local path for uploaded images (Covers). |
| `DB_PATH` | `./data/instance` | Local path for the SQLite database. |
| `SECRET_KEY` | `dev-key...` | Security key for sessions (should be changed). |

**Example `docker-compose.yml`:**

```yaml
version: '3.8'

services:
  web:
    build: .
    container_name: medienverwaltung
    ports:
      # Format: "HOST_PORT:CONTAINER_PORT"
      # Change the first value to adjust the port (e.g., "8080:5000")
      - "${APP_PORT:-5000}:5000"
    
    volumes:
      # Persistent data storage
      - ${UPLOAD_PATH:-./data/uploads}:/app/static/uploads
      - ${DB_PATH:-./data/instance}:/app/instance
    
    restart: unless-stopped
