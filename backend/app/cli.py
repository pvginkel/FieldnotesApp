"""CLI commands for application operations."""

import click
from dotenv import load_dotenv

from app import create_app


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """fieldnotes CLI."""
    ctx.ensure_object(dict)
    ctx.obj["app"] = create_app(skip_background_services=True)


def main() -> None:
    """Main CLI entry point."""
    # Load environment variables from .env file if present
    load_dotenv()

    # Register app-specific commands via hook
    from app.startup import register_cli_commands

    register_cli_commands(cli)

    cli()


if __name__ == "__main__":
    main()
