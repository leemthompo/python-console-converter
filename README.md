# Python console converter

CLI tool to add language tabs to Elasticsearch documentation markdown files. Replaces console code blocks with tab-sets containing Console and other language examples.

## Overview

This tool automates the conversion of Elasticsearch Console syntax code blocks into multi-language tab-sets, making documentation more accessible to developers working in different programming languages.

**Built on top of the excellent [`@elastic/request-converter`](https://www.npmjs.com/package/@elastic/request-converter)** - This tool is essentially a Python wrapper and workflow automation layer around the fantastic request-converter library, which does all the heavy lifting of converting Elasticsearch Console syntax into various programming languages.

## Features

- **Idempotent regeneration** - Safely regenerate language snippets without manual intervention
- **Snippet management** - Organizes code examples into reusable snippet files with include directives
- **Parallel processing** - Converts multiple languages concurrently using ThreadPoolExecutor for better performance
- **Multi-language support** - Supports curl, Python, JavaScript, PHP, and Ruby
- **ES|QL support** - Handles both Console and ES|QL code blocks
- **Code formatting and cleanup**:
  - Strips trailing whitespace from generated code
  - Formats curl commands with line breaks for readability
  - Removes annotation markers (`<1>`, `<2>`, etc.) and comment annotations from code before conversion
  - Preserves annotations as numbered lists after code blocks
- **Environment variable substitution** - Replaces hardcoded `http://localhost:9200` URLs with idiomatic environment variable syntax for each language
- **Smart boilerplate handling** - First code block includes client setup/imports, subsequent blocks strip boilerplate for cleaner examples
- **MyST directive nesting** - Automatically increments directive delimiters to maintain proper nesting when adding tab-sets
- **Sequential renumbering** - Regenerate mode renumbers snippets sequentially and updates include directives

## Requirements

### Required

**[`@elastic/request-converter`](https://www.npmjs.com/package/@elastic/request-converter)** - The core conversion library that powers all language transformations. Install globally via npm:

```bash
npm install -g @elastic/request-converter
```

This excellent library handles the complex task of converting Elasticsearch Console syntax into idiomatic code for multiple programming languages. Without it, this tool cannot function.

### Also required

- Python 3.x
- Python packages:
  ```bash
  pip install tqdm
  ```

## Installation

```bash
# Clone or copy the script
cp add-language-examples.py /your/project/

# Make it executable
chmod +x add-language-examples.py
```

## Usage

### Basic usage

Process a single markdown file:
```bash
./add-language-examples.py index-basics.md
```

Process all markdown files in a directory:
```bash
./add-language-examples.py ./docs/
```

### Specify target languages

Convert to specific languages only:
```bash
./add-language-examples.py index-basics.md -l python javascript
```

Default languages: `curl`, `python`, `js`, `php`, `ruby`

### Regenerate snippets

Regenerate language snippets from existing console snippets (idempotent):
```bash
./add-language-examples.py index-basics.md --regenerate
./add-language-examples.py ./docs/ -r
```

The regenerate mode:
- Deletes all language snippet files (keeps console/esql)
- Reads console snippets and renumbers them sequentially
- Regenerates language snippets for each console snippet
- Updates include directives in markdown if numbering changed
- Leaves markdown structure unchanged

## How it works

### First run (normal mode)

1. **Preflight check** - Skips files that already have language tabs
2. **Directive nesting** - Increments MyST directive delimiters to maintain proper nesting
3. **Block extraction** - Finds all `console` and `esql` code blocks (with optional numbered annotation lists)
4. **Snippet generation** - Creates organized snippet files in `_snippets/{filename}/`
5. **Annotation handling** - Strips annotation markers (`<1>`, `<2>`) from code before conversion, preserves numbered lists
6. **Language conversion** - Converts console syntax to target languages using `@elastic/request-converter`
7. **Code cleanup and formatting**:
   - Strips trailing whitespace
   - Formats curl with line breaks
   - Replaces hardcoded URLs with environment variables
8. **Tab-set creation** - Replaces original blocks with tab-sets using include directives
9. **Markdown update** - Writes the updated markdown file

### Regenerate mode

Designed for idempotent updates when you need to:
- Update to newer versions of language clients
- Fix conversion issues
- Change target languages
- Renumber snippets sequentially (fills gaps, updates include directives)

## Directory structure

```
your-docs/
├── index-basics.md           # Your markdown file
└── _snippets/
    └── index-basics/         # Snippets for index-basics.md
        ├── example1-console.md
        ├── example1-curl.md
        ├── example1-python.md
        ├── example1-js.md
        ├── example1-php.md
        ├── example1-ruby.md
        ├── example2-console.md
        └── ...
```

## Example transformation

### Before
``````markdown
```console
GET /my-index/_search
{
  "query": {
    "match_all": {}
  }
}
```
``````

### After
``````markdown
::::{tab-set}
:group: api-examples

:::{tab-item} Console
:sync: console

:::{include} _snippets/index-basics/example1-console.md
:::

:::{tab-item} curl
:sync: curl

:::{include} _snippets/index-basics/example1-curl.md
:::

:::{tab-item} Python
:sync: python

:::{include} _snippets/index-basics/example1-python.md
:::

... (other languages)

::::
``````

## Code formatting details

### curl formatting
Curl commands are formatted with line breaks for readability and the URL is moved to the first line:
```bash
curl -X POST "$ELASTICSEARCH_URL/my-index/_search" \
  -H "Content-Type: application/json" \
  -d '{
  "query": {
    "match_all": {}
  }
}'
```

### Environment variable substitution
Each language uses its idiomatic environment variable syntax:

- **curl**: `http://localhost:9200` → `$ELASTICSEARCH_URL`
- **Python**: `hosts=["http://localhost:9200"]` → `hosts=[os.getenv("ELASTICSEARCH_URL")]`
- **JavaScript**: `nodes: ["http://localhost:9200"]` → `nodes: [process.env["ELASTICSEARCH_URL"]]`
- **Ruby**: `host: "http://localhost:9200"` → `host: ENV["ELASTICSEARCH_URL"]`
- **PHP**: `->setHosts(["http://localhost:9200"])` → `->setHosts([getenv("ELASTICSEARCH_URL")])`

### Annotation handling
The tool preserves numbered annotation lists while stripping markers from code:

**Before conversion:**
```console
GET /my-index/_doc/1 <1>
```

1. Retrieves document with ID 1

**After conversion:**
- Code has `<1>` marker stripped before conversion
- Numbered list (1. Retrieves...) is preserved after the code block

## Options

```
usage: add-language-examples.py [-h] [-l LANGUAGES [LANGUAGES ...]] [-r] path

positional arguments:
  path                  Path to a markdown file or directory

options:
  -h, --help            Show help message
  -l, --languages       Target language(s) for conversion
  -r, --regenerate      Regenerate snippets from console snippets
```

## Limitations

- Non-recursive directory processing (processes only top-level `.md` files)
- Skips files that already contain language tabs (use `--regenerate` to update)

## Troubleshooting

### "es-request-converter not found"
The `@elastic/request-converter` library is not installed. Install it globally:
```bash
npm install -g @elastic/request-converter
```

### "Already contains tabs"
Use `--regenerate` to update existing snippets:
```bash
./add-language-examples.py your-file.md --regenerate
```

### Conversion errors
Check the error messages for specific language failures. The tool will complete processing and report which languages failed. Common causes:
- Unsupported Console syntax
- Complex queries that the converter doesn't handle
- Missing required fields in requests
