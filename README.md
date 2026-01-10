# Media Management v1.7.0

A web-based application for managing your physical media collection (CDs, vinyl records, books, movies, video games).
The system provides integrations with Discogs, Spotify, and Google Books to automatically load metadata and cover art.

## Features

* **Inventory**: Capture media items with barcode scanner support or manual entry.
* **Automatic metadata**:

  * **Music**: Search via the Discogs API (cover art, tracklists, year).
  * **Books**: Search via Google Books, OpenLibrary, and Amazon (cover art, author, description).
* **Spotify integration**: Preview player for CDs and vinyl records directly in the detail view.
* **Location management**: Hierarchical storage locations (e.g., Living Room > Shelf A > Compartment 1).
* **Lending status**: Mark items as lent out, including PDF export and print view.
* **QR codes**: Generate QR codes for each item for quick scanning.
* **Backup & restore**: Full backup of the database and images as a ZIP file.
* **User management**: Role-based access (Admin/User).

## Installation with Docker (Recommended)

The application is optimized for operation with Docker Compose.

### 1. Preparation

Make sure Docker and Docker Compose are installed.
Create a `docker-compose.yml` (or use the provided one) and start the container.

### 2. Configuration (Environment Variables)

Configuration is done via environment variables in the `docker-compose.yml` or a `.env` file.

| Variable      | Default           | Description                                             |
| :------------ | :---------------- | :------------------------------------------------------ |
| `APP_PORT`    | `5000`            | The port on which the web UI is accessible (host port). |
| `UPLOAD_PATH` | `./data/uploads`  | Local path for uploaded images (covers).                |
| `DB_PATH`     | `./data/instance` | Local path for the SQLite database.                     |
| `SECRET_KEY`  | `dev-key...`      | Security key for sessions (should be changed).          |

**Example `docker-compose.yml`:**

```yaml
version: '3.8'

services:
  web:
    build: .
    container_name: medienverwaltung
    ports:
      # Format: "HOST_PORT:CONTAINER_PORT"
      # Change the first value to adjust the port (e.g. "8080:5000")
      - "${APP_PORT:-5000}:5000"
    
    volumes:
      # Persistent data storage
      - ${UPLOAD_PATH:-./data/uploads}:/app/static/uploads
      - ${DB_PATH:-./data/instance}:/app/instance
    
    restart: unless-stopped
```

### 3. Start

```bash
docker-compose up -d
```

The application is now available at `http://localhost:5000` (or the configured port).

## First Login

On first startup, an administrator account is created automatically.

* **Username:** `admin`
* **Password:** `admin123`

> **Important:** Please change the password immediately after the first login under “Profile”.

## Setting Up External Services

To use all features, API keys should be stored in **Settings**:

1. **Discogs (for music metadata):**

   * Create an account on Discogs.
   * Generate a “Personal Access Token” under Developer Settings.
   * Enter it in the settings.

2. **Spotify (for player integration):**

   * Create an app in the Spotify Developer Dashboard.
   * Copy “Client ID” and “Client Secret”.
   * Enter them in the settings.

## Backup

In the admin area, you can download a complete backup at any time. It includes the SQLite database as well as all images. To restore, simply upload the ZIP file again.

## Changelog

### v1.7.0

* **Feature:** Central settings page (`/settings`) consolidates API, user, location, and backup management.
* **Feature:** Bulk move of media items to other locations (Bulk Move).
* **Security:** Passwords are now securely hashed (PBKDF2/SHA256). Automatic migration on login.
* **Security:** Deleting and moving items is now restricted to administrators.
* **UI:** New SVG logo and a cleaner menu bar.

### v1.6.0

* **Feature:** Lending overview (`/lent`) with PDF export for individual people or all items.
* **Feature:** Extended Spotify integration (play button, smart search via `difflib` for improved match rate).
* **Feature:** Global search now also finds track titles and borrower names.
* **UX:** Optimized mobile view for the capture form (more compact layout).
* **UX:** “Smart Location”: The last selected location is remembered when capturing new items.

### v1.5.0

* Initial Docker version with Discogs & Google Books support.
* Backup/restore system implemented.
