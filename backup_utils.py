import os
import shutil
import zipfile
import datetime
import time
from flask import current_app
from extensions import db

def create_backup_zip():
    """
    Erstellt ein ZIP-Archiv mit der Datenbank und dem Upload-Ordner.
    Gibt den Pfad zur ZIP-Datei zurück.
    """
    # 1. Pfade ermitteln
    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    
    if 'sqlite' not in db_uri:
        raise Exception("Backup funktioniert derzeit nur mit SQLite Datenbanken.")

    if '///' in db_uri:
        db_path = db_uri.split('///')[1]
    else:
        db_path = 'inventory.db'

    if not os.path.isabs(db_path):
        possible_path = os.path.join(current_app.instance_path, db_path)
        if not os.path.exists(possible_path):
            possible_path = os.path.join(current_app.root_path, db_path)
        db_path = possible_path

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Datenbankdatei nicht gefunden unter: {db_path}")

    upload_folder = current_app.config['UPLOAD_FOLDER']
    
    # 2. Dateinamen für Backup generieren
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"backup_inventory_{timestamp}.zip"
    backup_path = os.path.join(current_app.instance_path, backup_filename)
    
    if not os.path.exists(current_app.instance_path):
        os.makedirs(current_app.instance_path)

    # 3. Zip erstellen
    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(db_path, arcname='database.sqlite')
        
        if os.path.exists(upload_folder):
            for root, dirs, files in os.walk(upload_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.join('uploads', os.path.relpath(file_path, upload_folder))
                    zipf.write(file_path, arcname=arcname)
    
    return backup_path, backup_filename

def restore_backup_zip(zip_filepath):
    """
    Stellt Datenbank und Bilder aus einem ZIP wieder her.
    Nutzt copyfile statt move, um Docker-Mount-Probleme (Errno 16) zu vermeiden.
    """
    # 1. Pfade ermitteln
    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if 'sqlite' not in db_uri:
        raise Exception("Restore nur mit SQLite möglich.")
    
    if '///' in db_uri:
        db_file_name = db_uri.split('///')[1]
    else:
        db_file_name = 'inventory.db'

    if not os.path.isabs(db_file_name):
         target_db_path = os.path.join(current_app.instance_path, db_file_name)
         if not os.path.exists(target_db_path) and os.path.exists(os.path.join(current_app.root_path, db_file_name)):
             target_db_path = os.path.join(current_app.root_path, db_file_name)
    else:
        target_db_path = db_file_name

    upload_folder = current_app.config['UPLOAD_FOLDER']

    # 2. Verbindungen trennen
    # Wir versuchen, die Verbindung zur DB zu schließen
    db.session.remove()
    db.engine.dispose()
    
    # Kurze Pause, um dem OS Zeit zu geben, File-Locks zu lösen
    time.sleep(0.5)

    # 3. Zip prüfen und entpacken
    with zipfile.ZipFile(zip_filepath, 'r') as zipf:
        if 'database.sqlite' not in zipf.namelist():
            raise Exception("Ungültiges Backup-Archiv: 'database.sqlite' fehlt.")
        
        # Temp Folder erstellen
        temp_extract_path = os.path.join(current_app.instance_path, 'temp_restore')
        if os.path.exists(temp_extract_path):
            shutil.rmtree(temp_extract_path)
        os.makedirs(temp_extract_path)
        
        # Datenbank temporär entpacken
        zipf.extract('database.sqlite', temp_extract_path)
        extracted_db = os.path.join(temp_extract_path, 'database.sqlite')
        
        # A) Datenbank wiederherstellen
        # WICHTIG: shutil.copyfile statt shutil.move verwenden!
        # move löscht die Zieldatei (was bei Mounts verboten ist), copyfile überschreibt den Inhalt.
        
        # Backup der aktuellen DB (falls möglich)
        if os.path.exists(target_db_path):
            try:
                shutil.copyfile(target_db_path, target_db_path + ".bak")
            except OSError:
                print("Warnung: Konnte kein .bak der Datenbank erstellen (vielleicht auch gelockt/readonly).")

        # Neue DB drüberschreiben
        try:
            shutil.copyfile(extracted_db, target_db_path)
        except OSError as e:
            # Falls immer noch "Busy", ist die DB wahrscheinlich noch von einem Thread gelockt
            raise Exception(f"Datenbankdatei ist gesperrt. Bitte Server neu starten und erneut versuchen. Fehler: {e}")

        # B) Bilder extrahieren
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)
            
        for member in zipf.namelist():
            if member.startswith('uploads/'):
                filename = os.path.basename(member)
                if not filename: continue 
                
                source = zipf.open(member)
                target_path = os.path.join(upload_folder, filename)
                
                with source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)

        # Temp aufräumen
        shutil.rmtree(temp_extract_path)
        
    return True
