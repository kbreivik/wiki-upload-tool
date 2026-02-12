#!/usr/bin/env python3
"""
Upload markdown documentation files to Wiki.js via GraphQL API.

A general-purpose documentation uploader that works with any markdown project.

Usage:
    python upload_to_wikijs.py --wiki-url URL --base-path PATH --source-dir DIR [options]

The API key is loaded from .env file (WIKIJS_API_KEY) in the same directory,
or can be overridden with --api-key. Other settings can also be set via .env.

Options:
    --api-key KEY              Wiki.js API key (or WIKIJS_API_KEY in .env)
    --wiki-url URL             Wiki.js base URL (or WIKIJS_URL in .env)
    --base-path PATH           Wiki.js page path prefix (or WIKIJS_BASE_PATH in .env)
    --source-dir DIR           Directory with .md files (or WIKIJS_SOURCE_DIR in .env)
    --locale LOCALE            Wiki.js locale code (default: en, or WIKIJS_LOCALE in .env)
    --dry-run                  Show what would be uploaded without making changes
    --tag TAG                  Add tag to all pages (repeatable)
    --update-existing          Update pages that already exist (default: skip)
    --strip-footer-pattern PAT Regex pattern for footer removal (repeatable, optional)
    --index-file FILE          File to use as index page (default: README.md)

Frontmatter Support:
    Markdown files can include YAML frontmatter for metadata:
    ---
    title: Page Title
    description: Page description
    slug: custom-slug
    ---

    If no frontmatter is present, title is extracted from first # heading,
    and slug is generated from the filename.

Creates API key at: Wiki.js Admin > API Access > Create API Key
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error


def load_dotenv(env_path):
    """Load key=value pairs from a .env file into a dict."""
    env_vars = {}
    if not os.path.isfile(env_path):
        return env_vars
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                env_vars[key] = value
    return env_vars


def parse_frontmatter(content):
    """
    Parse YAML frontmatter from markdown content.

    Returns:
        tuple: (metadata_dict, content_without_frontmatter)
    """
    metadata = {}

    # Check if content starts with frontmatter delimiter
    if not content.startswith('---'):
        return metadata, content

    # Find the closing delimiter
    lines = content.split('\n')
    end_index = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == '---':
            end_index = i
            break

    if end_index is None:
        return metadata, content

    # Parse the frontmatter (simple key: value parsing)
    frontmatter_lines = lines[1:end_index]
    for line in frontmatter_lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            if value:
                metadata[key] = value

    # Return content without frontmatter
    content_lines = lines[end_index + 1:]
    # Remove leading blank lines
    while content_lines and not content_lines[0].strip():
        content_lines.pop(0)

    return metadata, '\n'.join(content_lines)


def discover_files(source_dir, index_file='README.md'):
    """
    Discover all markdown files in a directory.

    Returns:
        list: List of dicts with 'filename' and 'filepath' keys,
              sorted with index file first, then alphabetically.
    """
    files = []
    index_entry = None

    for filename in os.listdir(source_dir):
        if not filename.endswith('.md'):
            continue
        filepath = os.path.join(source_dir, filename)
        if not os.path.isfile(filepath):
            continue

        entry = {'filename': filename, 'filepath': filepath}

        if filename.lower() == index_file.lower():
            index_entry = entry
        else:
            files.append(entry)

    # Sort alphabetically
    files.sort(key=lambda x: x['filename'])

    # Put index file first
    if index_entry:
        files.insert(0, index_entry)

    return files


def generate_slug(filename, index_file='README.md'):
    """
    Generate a clean URL slug from a filename.

    Examples:
        README.md -> '' (empty for index)
        01-Oversikt.md -> 'Oversikt'
        Getting-Started.md -> 'Getting-Started'
        my_page.md -> 'my-page'
    """
    # Index file maps to empty slug (base path)
    if filename.lower() == index_file.lower():
        return ''

    # Remove .md extension
    slug = filename[:-3] if filename.lower().endswith('.md') else filename

    # Remove leading number prefix (e.g., 01-, 02-)
    slug = re.sub(r'^\d+[-_]', '', slug)

    # Replace underscores with hyphens
    slug = slug.replace('_', '-')

    return slug


def build_link_map(files, base_path, locale, index_file='README.md'):
    """Build a mapping from local .md filenames to wiki.js absolute paths."""
    link_map = {}
    for file_info in files:
        filename = file_info['filename']
        slug = file_info.get('slug', generate_slug(filename, index_file))

        if slug:
            wiki_path = f"/{locale}/{base_path}/{slug}"
        else:
            wiki_path = f"/{locale}/{base_path}"
        link_map[filename] = wiki_path

    return link_map


def rewrite_links(content, link_map):
    """Replace internal markdown links with wiki.js paths."""
    def replace_link(match):
        text = match.group(1)
        filename = match.group(2)
        if filename in link_map:
            return f"[{text}]({link_map[filename]})"
        return match.group(0)

    # Match [text](filename.md) patterns
    return re.sub(r'\[([^\]]+)\]\(([^)]+\.md)\)', replace_link, content)


def strip_footer_by_patterns(content, patterns):
    """
    Remove footer content matching any of the given regex patterns.

    Args:
        content: The markdown content
        patterns: List of regex patterns to match against trailing lines

    Returns:
        Content with matching footer lines removed
    """
    if not patterns:
        return content

    lines = content.rstrip().split('\n')

    # Compile patterns
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

    # Remove trailing lines that match any pattern
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue

        matched = False
        for pattern in compiled:
            if pattern.search(last):
                matched = True
                break

        if matched:
            lines.pop()
            # Also remove preceding --- separator and blank lines
            while lines and lines[-1].strip() in ('', '---'):
                lines.pop()
        else:
            break

    return '\n'.join(lines) + '\n'


def extract_title_from_content(content):
    """Extract the first # heading as title."""
    for line in content.split('\n'):
        line = line.strip()
        if line.startswith('# '):
            return line[2:].strip()
    return None


def graphql_request(wiki_url, api_key, query, variables=None):
    """Execute a GraphQL request against wiki.js."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        f"{wiki_url}/graphql",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8') if e.fp else ''
        print(f"  HTTP {e.code}: {body[:500]}")
        return None
    except urllib.error.URLError as e:
        print(f"  Connection error: {e.reason}")
        return None


def check_page_exists(wiki_url, api_key, path, locale):
    """Check if a page already exists at the given path."""
    query = """
    query ($path: String!, $locale: String!) {
      pages {
        singleByPath(path: $path, locale: $locale) {
          id
          title
        }
      }
    }
    """
    result = graphql_request(wiki_url, api_key, query, {
        "path": path,
        "locale": locale,
    })
    if result and result.get("data", {}).get("pages", {}).get("singleByPath"):
        return result["data"]["pages"]["singleByPath"]
    return None


def create_page(wiki_url, api_key, content, description, editor, is_published,
                is_private, locale, path, tags, title):
    """Create a new page via wiki.js GraphQL API."""
    query = """
    mutation (
      $content: String!,
      $description: String!,
      $editor: String!,
      $isPublished: Boolean!,
      $isPrivate: Boolean!,
      $locale: String!,
      $path: String!,
      $tags: [String]!,
      $title: String!
    ) {
      pages {
        create(
          content: $content,
          description: $description,
          editor: $editor,
          isPublished: $isPublished,
          isPrivate: $isPrivate,
          locale: $locale,
          path: $path,
          tags: $tags,
          title: $title
        ) {
          responseResult {
            succeeded
            errorCode
            slug
            message
          }
          page {
            id
            path
            title
          }
        }
      }
    }
    """
    variables = {
        "content": content,
        "description": description,
        "editor": editor,
        "isPublished": is_published,
        "isPrivate": is_private,
        "locale": locale,
        "path": path,
        "tags": tags,
        "title": title,
    }
    return graphql_request(wiki_url, api_key, query, variables)


def update_page(wiki_url, api_key, page_id, content, description, tags, title):
    """Update an existing page via wiki.js GraphQL API."""
    query = """
    mutation (
      $id: Int!,
      $content: String,
      $description: String,
      $tags: [String],
      $title: String
    ) {
      pages {
        update(
          id: $id,
          content: $content,
          description: $description,
          tags: $tags,
          title: $title
        ) {
          responseResult {
            succeeded
            errorCode
            message
          }
          page {
            id
            path
            title
          }
        }
      }
    }
    """
    variables = {
        "id": page_id,
        "content": content,
        "description": description,
        "tags": tags,
        "title": title,
    }
    return graphql_request(wiki_url, api_key, query, variables)


def process_file(file_info, link_map, args):
    """Process a single markdown file for upload."""
    filepath = file_info['filepath']
    filename = file_info['filename']

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse frontmatter
    metadata, content = parse_frontmatter(content)

    # Strip footer if patterns provided
    if args.strip_footer_pattern:
        content = strip_footer_by_patterns(content, args.strip_footer_pattern)

    # Rewrite internal links
    content = rewrite_links(content, link_map)

    # Generate slug
    slug = metadata.get('slug', generate_slug(filename, args.index_file))

    # Build wiki.js path
    if slug:
        path = f"{args.base_path}/{slug}"
    else:
        path = args.base_path

    # Get title: frontmatter > first heading > filename
    title = metadata.get('title') or extract_title_from_content(content) or filename[:-3]

    # Get description from frontmatter or empty
    description = metadata.get('description', '')

    return {
        "filename": filename,
        "filepath": filepath,
        "path": path,
        "slug": slug,
        "title": title,
        "description": description,
        "content": content,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Upload markdown documentation to Wiki.js via GraphQL API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload documentation to Wiki.js
  python upload_to_wikijs.py \\
    --wiki-url https://wiki.example.com \\
    --base-path Documentation/MyProject \\
    --source-dir content/myproject

  # Preview without uploading
  python upload_to_wikijs.py \\
    --wiki-url https://wiki.example.com \\
    --base-path Documentation/MyProject \\
    --source-dir content/myproject \\
    --dry-run

  # With Norwegian locale and tags
  python upload_to_wikijs.py \\
    --wiki-url https://wiki.example.com \\
    --base-path Dokumentasjon/Ansible \\
    --source-dir content/ansible \\
    --locale nb \\
    --tag ansible --tag documentation
"""
    )
    parser.add_argument("--api-key", default=None,
                        help="Wiki.js API key (or WIKIJS_API_KEY in .env)")
    parser.add_argument("--wiki-url", default=None,
                        help="Wiki.js base URL (or WIKIJS_URL in .env)")
    parser.add_argument("--base-path", default=None,
                        help="Wiki.js page path prefix (or WIKIJS_BASE_PATH in .env)")
    parser.add_argument("--source-dir", default=None,
                        help="Directory with .md files (or WIKIJS_SOURCE_DIR in .env)")
    parser.add_argument("--locale", default=None,
                        help="Wiki.js locale (default: en, or WIKIJS_LOCALE in .env)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be uploaded without changes")
    parser.add_argument("--tag", action="append", default=[],
                        help="Tag to add to all pages (repeatable)")
    parser.add_argument("--update-existing", action="store_true",
                        help="Update pages that already exist (default: skip)")
    parser.add_argument("--strip-footer-pattern", action="append", default=[],
                        help="Regex pattern for footer removal (repeatable)")
    parser.add_argument("--index-file", default="README.md",
                        help="File to use as index page (default: README.md)")

    args = parser.parse_args()

    # Load .env file (CWD first, then script dir as fallback)
    cwd_env = os.path.join(os.getcwd(), ".env")
    script_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    env_path = cwd_env if os.path.isfile(cwd_env) else script_env
    env_vars = load_dotenv(env_path)

    # Apply settings from .env if not provided via CLI
    if not args.api_key:
        args.api_key = env_vars.get("WIKIJS_API_KEY")
    if not args.wiki_url:
        args.wiki_url = env_vars.get("WIKIJS_URL")
    if not args.base_path:
        args.base_path = env_vars.get("WIKIJS_BASE_PATH")
    if not args.source_dir:
        args.source_dir = env_vars.get("WIKIJS_SOURCE_DIR")
    if not args.locale:
        args.locale = env_vars.get("WIKIJS_LOCALE", "en")

    # Validate required settings
    missing = []
    if not args.api_key or args.api_key == "your-api-key-here":
        missing.append("--api-key (or WIKIJS_API_KEY in .env)")
    if not args.wiki_url:
        missing.append("--wiki-url (or WIKIJS_URL in .env)")
    if not args.base_path:
        missing.append("--base-path (or WIKIJS_BASE_PATH in .env)")
    if not args.source_dir:
        missing.append("--source-dir (or WIKIJS_SOURCE_DIR in .env)")

    if missing:
        print("Error: Missing required settings:")
        for m in missing:
            print(f"  - {m}")
        print("\nProvide these via command line arguments or in .env file.")
        sys.exit(1)

    # Validate source directory exists
    if not os.path.isdir(args.source_dir):
        print(f"Error: Source directory not found: {args.source_dir}")
        sys.exit(1)

    # Strip trailing slash from URL
    args.wiki_url = args.wiki_url.rstrip('/')

    print(f"Wiki.js URL:    {args.wiki_url}")
    print(f"Base path:      {args.base_path}")
    print(f"Locale:         {args.locale}")
    print(f"Source dir:     {os.path.abspath(args.source_dir)}")
    print(f"Index file:     {args.index_file}")
    print(f"Tags:           {args.tag or '(none)'}")
    print(f"Footer patterns:{' ' + str(args.strip_footer_pattern) if args.strip_footer_pattern else ' (none)'}")
    print(f"Update existing:{' yes' if args.update_existing else ' no (skip)'}")
    if args.dry_run:
        print(f"Mode:           DRY RUN")
    print()

    # Discover markdown files
    files = discover_files(args.source_dir, args.index_file)

    if not files:
        print(f"No markdown files found in: {args.source_dir}")
        sys.exit(1)

    print(f"Found {len(files)} markdown file(s)")
    print("-" * 70)

    # Build link map for rewriting internal references
    # First pass: collect slugs
    file_infos = []
    for file_entry in files:
        filepath = file_entry['filepath']
        filename = file_entry['filename']

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        metadata, _ = parse_frontmatter(content)
        slug = metadata.get('slug', generate_slug(filename, args.index_file))

        file_infos.append({
            'filename': filename,
            'filepath': filepath,
            'slug': slug,
        })

    link_map = build_link_map(file_infos, args.base_path, args.locale, args.index_file)

    # Process all files
    pages = []
    for file_entry in files:
        file_info = {
            'filename': file_entry['filename'],
            'filepath': file_entry['filepath'],
        }
        page = process_file(file_info, link_map, args)
        pages.append(page)
        print(f"  /{args.locale}/{page['path']:<50} {page['title']}")

    print("-" * 70)
    print()

    if args.dry_run:
        print("=== DRY RUN - No changes will be made ===\n")
        for p in pages:
            print(f"[DRY RUN] Would upload: /{args.locale}/{p['path']}")
            print(f"          Title: {p['title']}")
            print(f"          Description: {p['description'] or '(none)'}")
            print(f"          Content length: {len(p['content'])} chars")
            print()
        print(f"Total: {len(pages)} pages")
        return

    # Test API connectivity
    print("Testing API connection...")
    test_result = graphql_request(args.wiki_url, args.api_key, "{ __typename }")
    if not test_result:
        print("Error: Could not connect to Wiki.js API. Check URL and API key.")
        sys.exit(1)
    print(f"  Connected to: {args.wiki_url}\n")

    # Upload pages
    created = 0
    updated = 0
    skipped = 0
    failed = 0

    for p in pages:
        print(f"Processing: {p['filename']} -> /{args.locale}/{p['path']}")

        # Check if page exists
        existing = check_page_exists(args.wiki_url, args.api_key, p["path"], args.locale)

        if existing:
            if args.update_existing:
                print(f"  Page exists (id: {existing['id']}), updating...")
                result = update_page(
                    args.wiki_url, args.api_key,
                    page_id=existing["id"],
                    content=p["content"],
                    description=p["description"],
                    tags=args.tag,
                    title=p["title"],
                )
                op = "update"
            else:
                print(f"  Page exists (id: {existing['id']}), skipping (use --update-existing to overwrite)")
                skipped += 1
                continue
        else:
            print(f"  Creating new page...")
            result = create_page(
                args.wiki_url, args.api_key,
                content=p["content"],
                description=p["description"],
                editor="markdown",
                is_published=True,
                is_private=False,
                locale=args.locale,
                path=p["path"],
                tags=args.tag,
                title=p["title"],
            )
            op = "create"

        if not result:
            print(f"  FAILED - no response")
            failed += 1
            continue

        pages_data = result.get("data", {}).get("pages", {})
        response = pages_data.get(op, {}).get("responseResult", {})

        if response.get("succeeded"):
            page_info = pages_data.get(op, {}).get("page", {})
            page_id = page_info.get("id", "?")
            if op == "create":
                print(f"  OK - created (id: {page_id})")
                created += 1
            else:
                print(f"  OK - updated (id: {page_id})")
                updated += 1
        else:
            error_code = response.get("errorCode", "unknown")
            message = response.get("message", "no details")
            print(f"  FAILED - {error_code}: {message}")
            failed += 1

    # Summary
    print()
    print("=" * 40)
    print(f"  Created:  {created}")
    print(f"  Updated:  {updated}")
    print(f"  Skipped:  {skipped}")
    print(f"  Failed:   {failed}")
    print(f"  Total:    {len(pages)}")
    print("=" * 40)

    if created + updated > 0:
        print(f"\nView at: {args.wiki_url}/{args.locale}/{args.base_path}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
