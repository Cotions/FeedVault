/* The `errors` list from POST /api/delete (or restore, purge): a one-line
   summary that expands into the file paths and reasons. `summary(n)` replaces
   the default line. */
export default function DeleteErrors({ errors, onDismiss, summary }) {
  if (!errors?.length) return null;
  const n = errors.length;
  return (
    <div className="delete-errors msg err" role="alert">
      <details>
        <summary>
          {summary ? summary(n) : `${n} file${n === 1 ? "" : "s"} could not be moved to the trash. The affected posts stay in the index.`}
        </summary>
        <ul>
          {errors.map((e, i) => (
            <li key={i}><code title={e.path}>{e.path}</code><span>{e.error}</span></li>
          ))}
        </ul>
      </details>
      {onDismiss && (
        <button type="button" className="btn-ghost delete-errors-close" onClick={onDismiss}>dismiss</button>
      )}
    </div>
  );
}
