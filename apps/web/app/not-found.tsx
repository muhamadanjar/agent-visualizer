import Link from "next/link";

export default function NotFoundPage() {
  return <main className="shell"><header className="masthead"><p className="eyebrow">Agent Office</p><h1>Page not found</h1><p className="lede">This route does not match an available workspace view.</p></header><section className="panel"><h2>Return to a known view</h2><p>Open the sample office or browse the workspace navigation.</p><Link className="button" href="/office">Open office</Link></section></main>;
}
