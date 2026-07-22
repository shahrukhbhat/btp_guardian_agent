## Testing CAP Applications

### Basic Setup

First, scaffold the test files by running:

```sh
cds add test
```

Replace then the test scaffolds with the actual test code. 

Rules:
- Call `cds.test()` **once per file**, at the top level, before any `describe` or `it` blocks
- `cds.test()` must be the first CDS call within the file, never load `cds.env` before `cds.test()`
- Write isolated test cases by preparing the use case with CQL, **never** rely on existing data
- Always use `cds.utils.uuid()` to generate UUIDs, **never** invent some UUIDs
- Never use `test.data.reset` to reset data, write isolated tests 

### Testing Custom Logic with CQL

Since `cds.test()` runs the server in-process, you can access services programmatically using CQL. Only test custom handler logic, **never** test generic CRUD that CAP handles out of the box.

- `srv.run(query)` — sends CQL through the application service, triggering all `on`/`before`/`after` handlers
- `await query` — bypasses handlers and calls the database directly (useful for seeding test data)

```js
const cds = require('@sap/cds')
const test = cds.test(__dirname + '/..')

let srv, Books, Orders

beforeAll(async () => {
  srv = await cds.connect.to('CatalogService');
  { Books, Orders } = srv.entities;
})

describe('BookShopService Testing', () => {
  it('should reject orders that exceed stock', async () => {
    // Seed a book with limited stock directly in the db (bypasses handlers)
    const ID = cds.utils.uuid()
    await INSERT.into(Books).entries({ ID, title: 'Limited Edition', stock: 2 })

    // Order through the service — custom before handler should reject
    const orderQuery = INSERT.into(Orders).entries({ book_ID: ID, quantity: 5 })
    await expect(srv.run(orderQuery)).rejects.toThrow(/stock/i)
  })

  it('should reduce stock after calling submitOrder action', async () => {
    const ID = cds.utils.uuid()
    await INSERT.into(Books).entries({ ID, title: 'CAP Guide', stock: 10 })

    await srv.send('submitOrder', { book: ID, quantity: 3 })

    const book = await srv.run(SELECT.one.from(Books).where({ ID }))
    expect(book.stock).toBe(7)
  })
})
```

### Running the Tests

- Always use "npm test" to run tests
- A full `npm test` run writes `test_report.json` at the project root via the Jest reporter installed by `project-init.sh`. Verify the file exists after the run; if it doesn't, re-run `npm test` without filters (no `-t`, no path args).
