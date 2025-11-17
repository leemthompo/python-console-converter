#!/usr/bin/env python3
"""
CLI tool to add language tabs to Elasticsearch documentation markdown files.
Replaces console code blocks with tab-sets containing Console and other language examples.
Requires `es-request-converter` installed globally via npm.
https://www.npmjs.com/package/@elastic/request-converter
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from tqdm import tqdm

DEFAULT_LANGUAGES = ["curl", "python", "js", "php", "ruby"] 

# Mapping from user-friendly names to es-request-converter format names
LANGUAGE_MAP = {
    "python": "Python",
    "javascript": "JavaScript",
    "js": "JavaScript",
    "php": "PHP",
    "ruby": "Ruby",
    "curl": "curl"
}

def extract_code_blocks(markdown_text, block_type):
    """Extract code blocks with their following annotation lists

    Returns list of tuples: (code_block, annotation_list)
    annotation_list is empty string if no annotations follow the block
    """
    # Pattern to match block followed by optional numbered list
    # Numbered list can be after 1 or 2 newlines
    # Numbered list stops before double newline or before text that doesn't start with a number
    pattern = fr"```{block_type}\n(.*?)\n```(?:\n\n(\d+\.[^\n]+(?:\n\d+\.[^\n]+)*))?"
    matches = re.findall(pattern, markdown_text, re.DOTALL)
    
    # Filter out result blocks and return tuples of (code, annotations)
    blocks = []
    for code, annotations in matches:
        if block_type != 'console' or not code.startswith('-result'):
            blocks.append((code, annotations.strip() if annotations else ''))
    return blocks


def esql_to_console(esql_content):
    """Convert ES|QL query to Console format by wrapping in POST /_query

    Args:
        esql_content: The ES|QL query code

    Returns:
        Console-formatted request string
    """
    # Wrap the ES|QL query in the _query API format
    console_format = f'''POST /_query?format=txt
{{
  "query": """
{esql_content}
  """
}}'''
    return console_format


from concurrent.futures import ThreadPoolExecutor, as_completed

def _convert_single_language(lang, console_content, complete, converter_lang):
    """Convert console code to a single target language."""
    try:
        cmd = ['es-request-converter', '--format', converter_lang, '--print-response']
        if complete:
            cmd.append('--complete')

        result = subprocess.run(
            cmd,
            input=console_content,
            text=True,
            capture_output=True,
            check=True
        )
        cleaned_code = '\n'.join(line.rstrip() for line in result.stdout.splitlines())
        return lang, cleaned_code, None
    except subprocess.CalledProcessError as e:
        error_lines = e.stderr.strip().split('\n')
        error_msg = None
        for line in error_lines:
            if 'Error:' in line or 'TypeError:' in line:
                error_msg = line.strip()
                break
        if not error_msg:
            error_msg = error_lines[0] if error_lines else "Conversion failed"
        return lang, console_content, error_msg
    except FileNotFoundError:
        error_msg = "es-request-converter not found (install: npm install -g @elastic/request-converter)"
        return lang, console_content, error_msg


def convert_console(console_content, language=None, complete=True):
    """Convert console syntax using es-request-converter (parallelized)."""
    # Normalize language parameter
    if language is None:
        languages = DEFAULT_LANGUAGES
    elif isinstance(language, str):
        languages = [language]
    else:
        languages = language
    
    results = {}
    errors = {}

    # Prepare conversion tasks
    tasks = []
    for lang in languages:
        converter_lang = LANGUAGE_MAP.get(lang.lower())
        if not converter_lang:
            errors[lang] = f"Unsupported language: {lang}"
            results[lang] = console_content
        else:
            tasks.append((lang, console_content, complete, converter_lang))
    
    # Run conversions in parallel
    with ThreadPoolExecutor(max_workers=len(tasks) * 1.5) as executor:
        futures = {executor.submit(_convert_single_language, *task): task[0] 
                   for task in tasks}
        
        for future in as_completed(futures):
            lang, code, error = future.result()
            results[lang] = code
            if error:
                errors[lang] = error
    
    return results, errors


def format_curl(code):
    """Format curl commands with line breaks for readability

    Places URL after method: curl -X POST "URL" \
      -H "header" \
      -d "data"

    Also replaces http://localhost:9200 with $ELASTICSEARCH_URL
    """
    # Replace localhost URL with environment variable
    code = re.sub(r'"http://localhost:9200', r'"$ELASTICSEARCH_URL', code)

    # Move URL to right after -X METHOD first
    code = re.sub(r'(curl -X \w+)(.*?)(\s+"[^"]+")$', r'\1\3\2', code, flags=re.MULTILINE)

    # Add line breaks before -H and -d flags
    formatted = re.sub(r' (-[Hd] )', r' \\\n  \1', code)

    return formatted


def format_python(code):
    """Fix Python code to use proper environment variable syntax

    Replaces: hosts=["http://localhost:9200"]
    With: hosts=[os.getenv("ELASTICSEARCH_URL")]
    """
    code = re.sub(
        r'hosts=\["http://localhost:9200"\]',
        r'hosts=[os.getenv("ELASTICSEARCH_URL")]',
        code
    )
    return code


def format_ruby(code):
    """Fix Ruby code to use proper environment variable syntax

    Replaces: host: "http://localhost:9200"
    With: host: ENV["ELASTICSEARCH_URL"]
    """
    code = re.sub(
        r'host:\s*["\']http://localhost:9200["\']',
        r'host: ENV["ELASTICSEARCH_URL"]',
        code
    )
    return code


def format_javascript(code):
    """Fix JavaScript code to use proper environment variable syntax

    Replaces: nodes: ["http://localhost:9200"]
    With: nodes: [process.env["ELASTICSEARCH_URL"]]
    """
    code = re.sub(
        r'nodes:\s*\["http://localhost:9200"\]',
        r'nodes: [process.env["ELASTICSEARCH_URL"]]',
        code
    )
    return code


def format_php(code):
    """Fix PHP code to use proper environment variable syntax

    Replaces: ->setHosts(["http://localhost:9200"])
    With: ->setHosts([getenv("ELASTICSEARCH_URL")])
    """
    code = re.sub(
        r'->setHosts\(\["http://localhost:9200"\]\)',
        r'->setHosts([getenv("ELASTICSEARCH_URL")])',
        code
    )
    return code


def strip_annotations(code):
    """Remove annotation markers from code

    Handles two formats:
    - Inline markers: <1>, <2>, etc.
    - Comment annotations: # comment text
    """
    # Remove <N> style markers
    code = re.sub(r'\s*<\d+>', '', code)
    # Remove # comment annotations
    code = re.sub(r'\s*#.*$', '', code, flags=re.MULTILINE)
    return code


def increment_directive_delimiters(text, levels=1):
    """Increment MyST directive nesting by adding colons to directives and closings

    Args:
        text: Markdown text with MyST directives
        levels: Number of colons to add (default: 1)

    Returns:
        Fixed markdown text with incremented nesting levels
    """
    directives = ['stepper', 'step', 'dropdown', 'note', 'warning', 'tip', 'important', 'plain', 'tabs', 'tab-set', 'tab-item']
    lines = text.split('\n')
    result = []
    directive_pattern = '|'.join(re.escape(d) for d in directives)
    add_colons = ':' * levels

    for line in lines:
        # Opening directive
        if re.match(rf'^(:+)\{{({directive_pattern})\}}', line):
            result.append(add_colons + line)
        # Closing (only colons)
        elif re.match(r'^(:{3,})$', line.strip()):
            ws = line[:len(line) - len(line.lstrip())]
            result.append(ws + add_colons + line.strip())
        else:
            result.append(line)

    return '\n'.join(result)

def prepare_code_for_conversion(code, block_type, annotations):
    """Prepare code and create the first tab based on block type.
    
    Args:
        code: The raw code block content
        block_type: Either 'console' or 'esql'
        annotations: Optional numbered list explaining code annotations
        
    Returns:
        tuple: (code_to_convert, first_tab_markdown)
    """
    code = code.strip()
    
    if block_type == 'esql':
        # Build ES|QL tab
        first_tab = f""":::{{tab-item}} ES|QL
:sync: esql
```esql
{code}
```
"""
        if annotations:
            first_tab += annotations + "\n"
        first_tab += ":::\n"
        
        # Convert to console format for other languages
        code_no_annotations = strip_annotations(code)
        code_to_convert = esql_to_console(code_no_annotations)
        
    else:  # console
        # Build Console tab
        first_tab = f""":::{{tab-item}} Console
:sync: console
```console
{code}
```
"""
        if annotations:
            first_tab += annotations + "\n"
        first_tab += ":::\n"
        
        # Strip annotations before converting
        code_to_convert = strip_annotations(code)
    
    return code_to_convert, first_tab


def write_snippet_file(snippets_dir, parent_filename, example_num, lang, code, annotations=''):
    """Write a code snippet to a file (code block only, no tab wrapper).

    Args:
        snippets_dir: Path to _snippets/{parent_filename} directory
        parent_filename: Name of parent markdown file (without .md) - used for include paths
        example_num: Example number (1-indexed)
        lang: Language identifier ('console', 'esql', 'python', 'curl', etc.)
        code: The code content
        annotations: Optional annotations to append after code block

    Returns:
        Path to the created snippet file
    """
    snippet_filename = f"example{example_num}-{lang}.md"
    snippet_path = snippets_dir / snippet_filename

    # Determine code language for syntax highlighting
    if lang == 'esql':
        code_lang = 'esql'
    elif lang == 'console':
        code_lang = 'console'
    elif lang == 'curl':
        code = format_curl(code)
        code_lang = 'bash'
    elif lang.lower() == 'python':
        code = format_python(code)
        code_lang = 'python'
    elif lang.lower() == 'ruby':
        code = format_ruby(code)
        code_lang = 'ruby'
    elif lang.lower() in ['js', 'javascript']:
        code = format_javascript(code)
        code_lang = 'js'
    elif lang.lower() == 'php':
        code = format_php(code)
        code_lang = 'php'
    else:
        code_lang = lang

    # Build snippet content - just the code block
    snippet_content = f"""```{code_lang}
{code}
```
"""

    if annotations:
        snippet_content += "\n" + annotations + "\n"

    # Write snippet file
    with open(snippet_path, 'w', encoding='utf-8') as f:
        f.write(snippet_content)

    return snippet_path


def build_include_directive(snippets_dir_name, parent_filename, example_num, lang):
    """Generate an include directive for a snippet.

    Args:
        snippets_dir_name: Name of snippets directory (e.g., '_snippets')
        parent_filename: Name of parent markdown file (without .md)
        example_num: Example number (1-indexed)
        lang: Language identifier

    Returns:
        String containing the include directive
    """
    snippet_filename = f"example{example_num}-{lang}.md"
    return f":::{{include}} {snippets_dir_name}/{parent_filename}/{snippet_filename}\n:::"


def build_language_tab(lang, converted_code):
    """Create markdown for a single language tab.

    Args:
        lang: Language identifier (e.g., 'python', 'curl')
        converted_code: The converted code for this language

    Returns:
        str: Markdown for the language tab
    """
    lang_label = LANGUAGE_MAP.get(lang.lower(), lang.capitalize())

    # Apply language-specific post-processing
    lang_lower = lang.lower()
    if lang == 'curl':
        converted_code = format_curl(converted_code)
    elif lang_lower == 'python':
        converted_code = format_python(converted_code)
    elif lang_lower == 'ruby':
        converted_code = format_ruby(converted_code)
    elif lang_lower in ['js', 'javascript']:
        converted_code = format_javascript(converted_code)
    elif lang_lower == 'php':
        converted_code = format_php(converted_code)

    # Use 'bash' syntax highlighting for curl
    code_lang = 'bash' if lang == 'curl' else lang

    return f"""
:::{{tab-item}} {lang_label}
:sync: {lang}
```{code_lang}
{converted_code}
```
:::
"""


def create_snippets_and_tabs(snippets_dir, parent_filename, example_num, code, annotations='',
                             languages=None, is_first_block=False, block_type='console'):
    """Create snippet files and generate tab-set with include directives.

    Args:
        snippets_dir: Path to _snippets directory
        parent_filename: Name of parent file (without .md extension)
        example_num: Example number (1-indexed)
        code: The code block content (console or esql)
        annotations: Optional numbered list explaining annotations
        languages: Target languages for conversion
        is_first_block: Whether this is the first block (keeps boilerplate)
        block_type: Type of code block - 'console' or 'esql'

    Returns:
        tuple: (tabs_markdown, errors) where errors is dict of {language: error_message}
    """
    if languages is None:
        languages = DEFAULT_LANGUAGES

    code = code.strip()
    errors = {}

    # Prepare code for conversion
    if block_type == 'esql':
        # Write ES|QL snippet
        write_snippet_file(snippets_dir, parent_filename, example_num, 'esql', code, annotations)

        # Convert to console format for other languages
        code_no_annotations = strip_annotations(code)
        code_to_convert = esql_to_console(code_no_annotations)

        # Write Console snippet
        write_snippet_file(snippets_dir, parent_filename, example_num, 'console', code_to_convert)
    else:  # console
        # Write Console snippet
        write_snippet_file(snippets_dir, parent_filename, example_num, 'console', code, annotations)

        # Strip annotations before converting
        code_to_convert = strip_annotations(code)

    # Convert to all target languages and write snippets
    converted_codes, conversion_errors = convert_console(code_to_convert, languages, complete=is_first_block)
    errors.update(conversion_errors)

    for lang in languages:
        write_snippet_file(snippets_dir, parent_filename, example_num, lang, converted_codes[lang])

    # Build tab-set with include directives
    tabs = "::::{tab-set}\n:group: api-examples\n\n"

    # Add ES|QL tab if this was an esql block
    if block_type == 'esql':
        tabs += ":::{tab-item} ES|QL\n:sync: esql\n\n"
        tabs += build_include_directive('_snippets', parent_filename, example_num, 'esql')
        tabs += "\n\n"

    # Add Console tab
    tabs += ":::{tab-item} Console\n:sync: console\n\n"
    tabs += build_include_directive('_snippets', parent_filename, example_num, 'console')
    tabs += "\n\n"

    # Add language tabs
    for lang in languages:
        lang_label = LANGUAGE_MAP.get(lang.lower(), lang.capitalize())
        sync_key = lang.lower()

        tabs += f":::{{tab-item}} {lang_label}\n:sync: {sync_key}\n\n"
        tabs += build_include_directive('_snippets', parent_filename, example_num, lang)
        tabs += "\n\n"

    tabs += "::::"

    return tabs, errors


def wrap_in_tabs(code, annotations='', languages=None, is_first_block=False, block_type='console'):
    """Wrap code block in tab-set with multiple language versions.

    Args:
        code: The code block content (console or esql)
        annotations: Optional numbered list explaining annotations
        languages: Target languages for conversion
        is_first_block: Whether this is the first block (keeps boilerplate)
        block_type: Type of code block - 'console' or 'esql'

    Returns:
        tuple: (tabs_markdown, errors) where errors is dict of {language: error_message}
    """
    if languages is None:
        languages = DEFAULT_LANGUAGES

    # Prepare code and build first tab (ES|QL or Console)
    code_to_convert, first_tab = prepare_code_for_conversion(code, block_type, annotations)

    # Start tab-set with first tab
    tabs = f"""::::{{tab-set}}
:group: api-examples

"""
    tabs += first_tab

    # Add Console tab for ES|QL blocks
    if block_type == 'esql':
        tabs += f"""
:::{{tab-item}} Console
:sync: console
```console
{code_to_convert}
```
:::
"""

    # Convert to all target languages
    converted_codes, conversion_errors = convert_console(code_to_convert, languages, complete=is_first_block)

    # Build language tabs
    for lang in languages:
        tabs += build_language_tab(lang, converted_codes[lang])

    tabs += "\n::::"
    return tabs, conversion_errors



def replace_blocks(markdown_text, console_replacements, esql_replacements):
    """Replace console and esql blocks (and their annotations) in markdown with tabbed versions

    Args:
        markdown_text: The markdown content
        console_replacements: List of tab-set replacements for console blocks
        esql_replacements: List of tab-set replacements for esql blocks

    Returns:
        Updated markdown text
    """
    # First, replace console blocks
    console_pattern = r"```console\n(.*?)\n```(?:\n\n(\d+\.[^\n]+(?:\n\d+\.[^\n]+)*))?"
    console_iter = iter(console_replacements)

    def console_replacer(match):
        content = match.group(1)
        if content.startswith('-result'):
            return match.group(0)  # Return original, don't replace
        return next(console_iter)

    markdown_text = re.sub(console_pattern, console_replacer, markdown_text, flags=re.DOTALL)

    # Then, replace esql blocks
    esql_pattern = r"```esql\n(.*?)\n```(?:\n\n(\d+\.[^\n]+(?:\n\d+\.[^\n]+)*))?"
    esql_iter = iter(esql_replacements)

    def esql_replacer(match):
        return next(esql_iter)

    markdown_text = re.sub(esql_pattern, esql_replacer, markdown_text, flags=re.DOTALL)

    return markdown_text


def has_console_snippets(snippets_dir):
    """Check if console snippet files exist in the snippets directory

    Args:
        snippets_dir: Path to _snippets/{filename} directory

    Returns:
        bool: True if any example*-console.md files exist
    """
    if not snippets_dir.exists():
        return False
    return len(list(snippets_dir.glob('example*-console.md'))) > 0


def get_console_snippets(snippets_dir):
    """Get all console snippet files sorted by example number

    Args:
        snippets_dir: Path to _snippets/{filename} directory

    Returns:
        list: Sorted list of (example_num, snippet_path) tuples
    """
    console_files = list(snippets_dir.glob('example*-console.md'))

    # Extract example numbers and sort
    snippets = []
    for path in console_files:
        # Parse example number from filename like "example3-console.md"
        match = re.match(r'example(\d+)-console\.md', path.name)
        if match:
            example_num = int(match.group(1))
            snippets.append((example_num, path))

    return sorted(snippets, key=lambda x: x[0])


def parse_snippet_file(snippet_path):
    """Parse a snippet file to extract code and annotations

    Args:
        snippet_path: Path to snippet file

    Returns:
        tuple: (code, annotations) where annotations may be empty string
    """
    with open(snippet_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract code block
    code_match = re.search(r'```(?:console|esql)\n(.*?)\n```', content, re.DOTALL)
    if not code_match:
        return '', ''

    code = code_match.group(1)

    # Extract annotations (numbered list after code block)
    annotations_match = re.search(r'```\n\n(\d+\.[^\n]+(?:\n\d+\.[^\n]+)*)', content)
    annotations = annotations_match.group(1).strip() if annotations_match else ''

    return code, annotations


def clean_language_snippets(snippets_dir, keep_languages=['console', 'esql']):
    """Delete all language snippet files except specified ones

    Args:
        snippets_dir: Path to _snippets/{filename} directory
        keep_languages: List of language extensions to keep (default: console, esql)

    Returns:
        int: Number of files deleted
    """
    if not snippets_dir.exists():
        return 0

    deleted_count = 0
    for snippet_file in snippets_dir.glob('example*.md'):
        # Check if this is a file we want to keep
        should_keep = False
        for lang in keep_languages:
            if snippet_file.name.endswith(f'-{lang}.md'):
                should_keep = True
                break

        if not should_keep:
            snippet_file.unlink()
            deleted_count += 1

    return deleted_count


def count_tabsets_in_markdown(markdown_text):
    """Count the number of tab-sets in markdown

    Args:
        markdown_text: The markdown content

    Returns:
        int: Number of ::::{tab-set} blocks found
    """
    tabset_pattern = r'::::\{tab-set\}'
    return len(re.findall(tabset_pattern, markdown_text))


def regenerate_from_snippets(filepath, languages=None):
    """Regenerate language snippets from existing console snippets

    This is the IDEMPOTENT workflow:
    1. Deletes all language snippet files (keeps console/esql)
    2. Reads all console snippets and renumbers them sequentially
    3. Regenerates language snippets for each console snippet
    4. Markdown remains unchanged (assumes tab-sets already exist)
    5. Warns if snippet count doesn't match tab-set count in markdown

    Args:
        filepath: Path to markdown file
        languages: Target languages (defaults to DEFAULT_LANGUAGES)

    Returns:
        bool: True if successful
    """
    target_langs = languages if languages else DEFAULT_LANGUAGES
    parent_filename = filepath.stem
    snippets_dir = filepath.parent / '_snippets' / parent_filename

    print(f"\n{'='*60}")
    print(f"📄 File: {filepath.name}")
    print(f"🔄 Regenerating from console snippets")
    print(f"🎯 Target languages: {', '.join(target_langs)}")
    print(f"{'='*60}")

    # Check if console snippets exist
    if not has_console_snippets(snippets_dir):
        print("❌ No console snippets found")
        return False

    # Clean ALL snippet files (we'll regenerate console snippets with clean numbering)
    deleted_count = 0
    for snippet_file in snippets_dir.glob('example*.md'):
        deleted_count += 1
    print(f"🗑️  Cleaning {deleted_count} old snippet file(s)")

    # Get all console snippets BEFORE deleting
    console_snippets = get_console_snippets(snippets_dir)
    print(f"🔍 Found {len(console_snippets)} console snippet(s)")

    # Read console snippet data before cleaning
    console_data = []  # List of (code, annotations)
    for example_num, snippet_path in console_snippets:
        code, annotations = parse_snippet_file(snippet_path)
        if code:
            console_data.append((code, annotations))

    # Check for numbering gaps
    expected_numbers = list(range(1, len(console_snippets) + 1))
    actual_numbers = [num for num, _ in console_snippets]

    if actual_numbers != expected_numbers:
        print(f"🔢 Renumbering snippets sequentially: {actual_numbers} → {expected_numbers}")

    # Clean all snippets
    for snippet_file in snippets_dir.glob('example*.md'):
        snippet_file.unlink()

    # Read markdown and check tab-set count
    with open(filepath, 'r', encoding='utf-8') as f:
        markdown_text = f.read()

    tabset_count = count_tabsets_in_markdown(markdown_text)

    all_errors = {}

    # Process each console snippet with sequential numbering
    for new_num, (console_code, annotations) in enumerate(tqdm(console_data, desc="   ⏳ Regenerating snippets", unit="snippet"), start=1):
        # Write console snippet with new sequential number
        write_snippet_file(snippets_dir, parent_filename, new_num, 'console', console_code, annotations)

        # Strip annotations before converting
        code_to_convert = strip_annotations(console_code)

        # Convert to all target languages
        # First snippet gets complete=True to keep boilerplate
        is_first = (new_num == 1)
        converted_codes, conversion_errors = convert_console(code_to_convert, target_langs, complete=is_first)

        if conversion_errors:
            all_errors[new_num] = conversion_errors

        # Write language snippets with new sequential number
        for lang in target_langs:
            write_snippet_file(snippets_dir, parent_filename, new_num, lang, converted_codes[lang])

    # Update include directives in markdown to use new numbering
    if actual_numbers != expected_numbers or len(console_data) != tabset_count:
        print(f"📝 Updating include directives in markdown...")

        # Update include paths to use new sequential numbering
        # Pattern: :::include _snippets/{parent}/exampleN-{lang}.md
        for old_num, new_num in zip(actual_numbers, expected_numbers):
            if old_num != new_num:
                # Replace old example number with new in include directives
                old_pattern = rf'(_snippets/{parent_filename}/example){old_num}(-\w+\.md)'
                new_replacement = rf'\g<1>{new_num}\g<2>'
                markdown_text = re.sub(old_pattern, new_replacement, markdown_text)

        # Write updated markdown
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_text)

        print(f"✅ Updated include directives in markdown")

    # Report results
    if all_errors:
        print(f"\n⚠️  Completed with errors:")
        for example_num, errors in all_errors.items():
            print(f"   Example {example_num}:")
            for lang, error_msg in errors.items():
                print(f"      • {lang}: {error_msg}")
        print()
        return False
    else:
        print(f"✅ Successfully regenerated {len(console_data)} snippet(s)\n")
        return True


def has_language_tabs(markdown_text, languages):
    """Check if the markdown already contains tabs for the specified languages or ES|QL tabs"""
    if languages is None:
        languages = DEFAULT_LANGUAGES

    # Check for ES|QL tabs
    esql_pattern = r':::+\{tab-item\}\s+ES\|QL\s*\n\s*:sync:\s+esql'
    if re.search(esql_pattern, markdown_text, re.IGNORECASE):
        return True, 'esql'

    for lang in languages:
        # Check for language-specific tab items
        # Look for the pattern: "::::{tab-item} Python" followed by ":sync: python"
        lang_label = lang.capitalize()
        # Must have both the tab-item AND sync on the next line to be a language tab
        pattern = r':::+\{tab-item\}\s+' + re.escape(lang_label) + r'\s*\n\s*:sync:\s+' + re.escape(lang.lower())
        if re.search(pattern, markdown_text, re.IGNORECASE):
            return True, lang

    return False, None

def process_file(filepath, languages=None, regenerate=False):
    """Process a single markdown file"""
    target_langs = languages if languages else DEFAULT_LANGUAGES

    # Handle regenerate mode
    if regenerate:
        snippets_dir = filepath.parent / '_snippets' / filepath.stem
        if not has_console_snippets(snippets_dir):
            print(f"❌ No console snippets found for {filepath.name}")
            print(f"   Run without --regenerate to create snippets first.")
            return False
        return regenerate_from_snippets(filepath, languages)

    # Normal processing mode
    print(f"\n{'='*60}")
    print(f"📄 File: {filepath.name}")
    print(f"🎯 Target languages: {', '.join(target_langs)}")
    print(f"{'='*60}")

    # Read the file
    with open(filepath, 'r', encoding='utf-8') as f:
        markdown_text = f.read()

    # Preflight check
    has_tabs, found_lang = has_language_tabs(markdown_text, languages)
    if has_tabs:
        print(f"⚠️  Already contains {found_lang} tabs (use --regenerate to update)")
        return False

    # Increment existing directive nesting BEFORE adding new tab-sets
    # This ensures existing nested directives maintain correct nesting after we add outer tab-sets
    print(f"🔧 Incrementing directive delimiters (adding colons)...")
    markdown_text = increment_directive_delimiters(markdown_text, levels=1)

    # Extract console and esql blocks
    console_blocks = extract_code_blocks(markdown_text, 'console')
    esql_blocks = extract_code_blocks(markdown_text, 'esql')

    if not console_blocks and not esql_blocks:
        print(f"ℹ️  No console or esql blocks found")
        return False

    if console_blocks:
        print(f"🔍 Found {len(console_blocks)} console block(s)")
    if esql_blocks:
        print(f"🔍 Found {len(esql_blocks)} esql block(s)")

    # Get parent filename without extension
    parent_filename = filepath.stem

    # Create _snippets/{filename}/ directory structure
    snippets_base_dir = filepath.parent / '_snippets'
    snippets_dir = snippets_base_dir / parent_filename

    if snippets_dir.exists():
        print(f"📁 Found snippets directory: _snippets/{parent_filename}/")
    else:
        snippets_dir.mkdir(parents=True, exist_ok=True)
        print(f"📁 Created snippets directory: _snippets/{parent_filename}/")

    # Convert console blocks and collect errors
    console_tabs = []
    all_errors = {}  # {('console'|'esql', block_num): {lang: error_msg}}

    if console_blocks:
        for i, (console_code, annotations) in enumerate(tqdm(console_blocks, desc="   ⏳ Converting console blocks", unit="block")):
            tab, errors = create_snippets_and_tabs(
                snippets_dir, parent_filename, i + 1, console_code, annotations,
                languages, is_first_block=(i == 0), block_type='console'
            )
            console_tabs.append(tab)
            if errors:
                all_errors[('console', i + 1)] = errors

    # Convert esql blocks and collect errors
    esql_tabs = []

    if esql_blocks:
        for i, (esql_code, annotations) in enumerate(tqdm(esql_blocks, desc="   ⏳ Converting esql blocks", unit="block")):
            tab, errors = create_snippets_and_tabs(
                snippets_dir, parent_filename, len(console_blocks) + i + 1, esql_code, annotations,
                languages, is_first_block=(i == 0 and not console_blocks), block_type='esql'
            )
            esql_tabs.append(tab)
            if errors:
                all_errors[('esql', i + 1)] = errors

    # Replace blocks
    print(f"📝 Updating markdown...")
    updated_markdown = replace_blocks(markdown_text, console_tabs, esql_tabs)

    # Write file
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(updated_markdown)

    # Report results
    if all_errors:
        print(f"\n⚠️  Completed with errors in {filepath.name}:")
        for (block_type, block_num), errors in all_errors.items():
            print(f"   {block_type.capitalize()} block {block_num}:")
            for lang, error_msg in errors.items():
                print(f"      • {lang}: {error_msg}")
        print()
        return False
    else:
        print(f"✅ Successfully updated {filepath.name}\n")
        return True


def process_directory(dirpath, languages=None, regenerate=False):
    """Process all markdown files in a directory (non-recursive)"""
    md_files = list(Path(dirpath).glob('*.md'))

    if not md_files:
        print(f"❌ No markdown files found in {dirpath}")
        return

    target_langs = languages if languages else DEFAULT_LANGUAGES
    mode = "Regenerating" if regenerate else "Processing"

    print(f"\n{'='*60}")
    print(f"📁 Directory: {dirpath}")
    print(f"📄 Found {len(md_files)} markdown file(s)")
    print(f"🎯 Target languages: {', '.join(target_langs)}")
    print(f"🔄 Mode: {mode}")
    print(f"{'='*60}\n")

    updated_count = 0
    skipped_count = 0

    for filepath in md_files:
        if process_file(filepath, languages, regenerate):
            updated_count += 1
        else:
            skipped_count += 1

    print(f"\n{'='*60}")
    print(f"📊 Summary:")
    print(f"   ✅ Updated: {updated_count} file(s)")
    print(f"   ⏭️  Skipped: {skipped_count} file(s)")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Add language tabs to Elasticsearch documentation markdown files',
        epilog="""
Examples:
  # Process single file with default languages
  %(prog)s index-basics.md

  # Process directory with default languages
  %(prog)s ./docs/

  # Convert to specific languages
  %(prog)s index-basics.md -l python javascript ruby
  %(prog)s ./docs/ --languages python javascript

  # Regenerate language snippets from console snippets
  %(prog)s index-basics.md --regenerate
  %(prog)s ./docs/ -r
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        'path',
        help='Path to a markdown file or directory containing markdown files'
    )
    parser.add_argument(
        '-l', '--languages',
        nargs='+',
        help=f'Target language(s) for conversion (default: {", ".join(DEFAULT_LANGUAGES)}). '
             'Can specify multiple languages separated by spaces (e.g., -l python javascript)'
    )
    parser.add_argument(
        '-r', '--regenerate',
        action='store_true',
        help='Regenerate language snippets from console snippets (deletes all non-console snippets, then regenerates)'
    )

    args = parser.parse_args()
    path = Path(args.path)

    if not path.exists():
        print(f"❌ Error: {path} does not exist")
        sys.exit(1)

    # Determine languages to use
    languages = args.languages if args.languages else None

    if path.is_file():
        if path.suffix != '.md':
            print(f"❌ Error: {path} is not a markdown file")
            sys.exit(1)
        process_file(path, languages, args.regenerate)
    elif path.is_dir():
        process_directory(path, languages, args.regenerate)
    else:
        print(f"❌ Error: {path} is neither a file nor directory")
        sys.exit(1)

if __name__ == '__main__':
    main()