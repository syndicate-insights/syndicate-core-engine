{{
    config(
        materialized='incremental',
        unique_key='file_name',
        on_schema_change='append_new_columns'
    )
}}

/*
    Metadata table that tracks which GCS files have already been processed.
    Used to filter out previously ingested files.
*/

{% if is_incremental() %}

    select
        file_name,
        processed_at
    from {{ this }}

{% else %}

    select
        cast(null as string) as file_name,
        cast(null as timestamp) as processed_at
    order by file_name
    limit 0

{% endif %}
