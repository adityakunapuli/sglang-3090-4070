"""CLI package — keeps the console entry points ``myhealth`` / ``anthem`` working."""

import click

# 1) Load the core module (groups + factories), then command modules (registration side effects).
import myhealth_fhir.cli.auth as _auth  # noqa: F401  (auth factory; wiring happens in main)
import myhealth_fhir.cli.main as _main_mod  # noqa: F401  (defines main/anthem/ucla/db groups)
import myhealth_fhir.cli.anthem as _anthem_mod  # noqa: F401  (registers @anthem commands)
import myhealth_fhir.cli.ucla as _ucla_mod  # noqa: F401  (registers @ucla commands)
import myhealth_fhir.cli.search as _search_mod  # noqa: F401  (registers @main search commands)

# 2) Bind the click groups (post-registration) — NOT before, submodule imports shadow these names.
from myhealth_fhir.cli.main import anthem, main


# 3) Provider-less entry point: flatten anthem commands to top level.
@click.group(invoke_without_command=True)
@click.pass_context
def anthem_cli(ctx):
    """Anthem/Elevance Health CLI — insurance data, EOB, coverage."""
    ctx.ensure_object(dict)
    ctx.obj["provider"] = "anthem"
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Copy all anthem-provider commands to top level (auth, fhir, eob, patients, etc.)
for _name, _cmd in list(anthem.commands.items()):
    anthem_cli.add_command(_cmd)

# Copy shared top-level commands (provider-agnostic)
for _name in ("mcd", "formulary", "job", "server", "search-claims", "search-eob"):
    anthem_cli.add_command(main.commands[_name])


def anthem_main():
    """Entry point for the ``anthem`` binary. Provider subcommand not needed."""
    anthem_cli.main()


__all__ = ["main", "anthem_main"]
