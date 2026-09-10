Referral Monitor v7.3.1 hotfix

Fixes a malformed Typer option declaration in watch-url introduced in v7.3.
The bug caused every CLI command, including status, to crash with:
AttributeError: 'bool' object has no attribute 'isidentifier'

Also adds a regression test that builds the Typer CLI command tree.
