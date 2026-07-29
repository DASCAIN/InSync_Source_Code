import re
import os

input_file = r"f:\Insync\Updated_Insync_Website\dascailearing-b0ed1c23-main\src\routes\index.tsx"
output_file = r"f:\Insync\insync_source\frontend\templates\new_landing.html"

with open(input_file, 'r', encoding='utf-8') as f:
    content = f.read()

# Very basic extraction of return blocks from components
# This is a heuristic and will require manual cleanup

# Replace className with class
content = content.replace('className=', 'class=')

# Replace {children} with generic children (we will assemble manually)
content = content.replace('{children}', '')

print("Done")
