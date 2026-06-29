#!/bin/bash

set -u

BASE="$HOME/scripts/antenna_designer_app"
DATA_DIR="$BASE/data"
NEC_DIR="$BASE/nec_files"

FILES=(
  "app.py"
  "models.py"
  "builder.py"
  "db.py"
  "backends.py"
  "tuner.py"
  "analysis.py"
  "nec_writer.py"
)

echo "--------------------------------------------"
echo "Antenna Designer Project Folder Setup"
echo "--------------------------------------------"
echo "This will create:"
echo "  $BASE"
echo "  $DATA_DIR"
echo "  $NEC_DIR"
echo
echo "It will also COPY matching project files from:"
echo "  $HOME/scripts"
echo
echo "It will NOT remove other projects."
echo "It will NOT overwrite files already in the new folder."
echo "--------------------------------------------"
echo

read -p "Continue? [y/N]: " ANSWER
ANSWER=${ANSWER,,}

if [[ "$ANSWER" != "y" && "$ANSWER" != "yes" ]]; then
  echo "Cancelled."
  exit 0
fi

mkdir -p "$BASE"
mkdir -p "$DATA_DIR"
mkdir -p "$NEC_DIR"

echo
echo "Created folders:"
echo "  $BASE"
echo "  $DATA_DIR"
echo "  $NEC_DIR"
echo

SOURCE_DIR="$HOME/scripts"

echo "Copying project files if found..."
for FILE in "${FILES[@]}"; do
  if [[ -f "$SOURCE_DIR/$FILE" ]]; then
    if [[ -f "$BASE/$FILE" ]]; then
      echo "  SKIP: $FILE already exists in project folder"
    else
      cp "$SOURCE_DIR/$FILE" "$BASE/$FILE"
      echo "  COPIED: $FILE"
    fi
  else
    echo "  MISSING: $FILE not found in $SOURCE_DIR"
  fi
done

if [[ -f "$SOURCE_DIR/antenna_designer.db" ]]; then
  if [[ -f "$DATA_DIR/antenna_designer.db" ]]; then
    echo "  SKIP: antenna_designer.db already exists in data folder"
  else
    cp "$SOURCE_DIR/antenna_designer.db" "$DATA_DIR/antenna_designer.db"
    echo "  COPIED: antenna_designer.db -> data/"
  fi
fi

echo
echo "Writing a small run helper script..."
cat > "$BASE/run.sh" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 app.py
EOF
chmod +x "$BASE/run.sh"

echo
echo "Done."
echo
echo "Project folder:"
echo "  $BASE"
echo
echo "To open it:"
echo "  cd $BASE"
echo
echo "To see files:"
echo "  ls -lah"
echo
echo "To run the app:"
echo "  ./run.sh"
echo "or"
echo "  python3 app.py"
echo
