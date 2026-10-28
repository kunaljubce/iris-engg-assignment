"""Command line: `harvester run [--dry-run] [--source NAME] [--city SLUG]`, `harvester report`."""

import logging

import click
import httpx
from pydantic import ValidationError

from harvester.adapters.fetch import make_client
from harvester.config import HarvesterConfig, SachsenSource, load_config
from harvester.db.engine import DatabaseConfigError, make_engine
from harvester.report import quality_report
from harvester.runner import RunSummary, run_source, scrape_and_normalise


@click.group()
def cli() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


@cli.command()
@click.option("--dry-run", is_flag=True, help="Scrape and normalise, print, write nothing.")
@click.option("--source", "source_name", help="Run only this source (name in settings.yml).")
@click.option("--city", help="Run only this city (e.g. dresden).")
def run(dry_run: bool, source_name: str | None, city: str | None) -> None:
    """Scrape, filter and normalise every configured source, then upsert into the database."""
    try:
        config = load_config()
    except ValidationError as exc:
        raise click.ClickException(f"config/settings.yml is invalid:\n{exc}") from exc
    sources = select_sources(config, source_name, city)

    with make_client(config.scraping) as client:
        if dry_run:
            for source in sources:
                _print_dry_run(client, source, config)
            return

        try:
            engine = make_engine()
        except DatabaseConfigError as exc:
            raise click.ClickException(str(exc)) from exc
        summaries = [run_source(engine, client, source, config) for source in sources]
        engine.dispose()

    for summary in summaries:
        _print_summary(summary)
    if any(s.status == "failed" for s in summaries):
        # Non-zero exit, so a scheduler sees the failure; the details are in ingest_run
        raise SystemExit(1)


@cli.command()
def report() -> None:
    """Print the quality report: latest run per source and the stored records."""
    try:
        engine = make_engine()
    except DatabaseConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    with engine.connect() as conn:
        click.echo(quality_report(conn))
    engine.dispose()


def select_sources(
    config: HarvesterConfig, source_name: str | None, city: str | None
) -> list[SachsenSource]:
    """The configured sources, narrowed to --source and --city. An unknown name is an error,
    not an empty run."""
    sources = config.sources
    if source_name is not None:
        sources = [s for s in sources if s.name == source_name]
        if not sources:
            names = ", ".join(s.name for s in config.sources)
            raise click.BadParameter(f"unknown source {source_name!r} (one of: {names})")
    if city is None:
        return list(sources)

    selected: list[SachsenSource] = []
    for source in sources:
        cities = [c for c in source.cities if c.slug == city]
        if cities:
            selected.append(source.model_copy(update={"cities": cities}))
    if not selected:
        raise click.BadParameter(f"no selected source covers city {city!r}")
    return selected


def _print_dry_run(client: httpx.Client, source: SachsenSource, config: HarvesterConfig) -> None:
    scraped, normalised = scrape_and_normalise(client, source, config)
    kept = {r.source_record_id: r for r in normalised.records}
    click.echo(
        f"\n{source.name}: {len(scraped.records)} scraped, {len(kept)} industrial,"
        f" {normalised.filtered_out} filtered out, {len(scraped.errors)} errors"
    )
    for scraped_record in scraped.records:
        record = kept.get(scraped_record.source_record_id)
        if record is None:
            click.echo(f"  -    {scraped_record.title}")
            continue
        click.echo(
            f"  KEEP {record.title}\n"
            f"       keywords={record.matched_keywords} date={record.publication_date}"
            f" geocode={record.geocode_status} flags={record.quality_flags}"
        )
    for error in scraped.errors:
        click.echo(f"  ERROR {error.kind}: {error.message} ({error.url})")


def _print_summary(s: RunSummary) -> None:
    click.echo(
        f"{s.source}: {s.status} (run {s.run_id}) scraped={s.scraped}"
        f" filtered_out={s.filtered_out} rejected={s.rejected} inserted={s.inserted}"
        f" updated={s.updated} errors={len(s.errors)}"
        + (f"\n  {s.error_message}" if s.error_message else "")
    )


def main() -> None:
    cli()
