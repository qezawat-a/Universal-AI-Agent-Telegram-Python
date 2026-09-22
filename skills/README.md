# J-Rock skills directory — two layouts, both auto-loaded:

1) Folder convention (recommended, no renaming needed):
   skills/<anything>/SKILL.md   (also accepts skill.md or <anything>.md)
   Skill name comes from frontmatter `name:` — or the folder name.
   Example frontmatter:
     ---
     name: xt-market
     description: XT market data queries...
     ---

2) Single file:
   skills/<name>.md  (first `# Title` = skill name)

Use: /skill list shows all, /skill use <name> activates,
/skill import <folder|file> copies one into your DB skills.
