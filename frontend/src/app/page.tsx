import Link from "next/link";
import { Card } from "@/components/ui/card";

export default function Home() {
  return (
    <div className="space-y-6">
      <h1>ProspectForge</h1>
      <p className="text-neutral-600">
        Acme&apos;s internal contact-sourcing tool. Paste a curated URL, get back a
        reviewed, evidence-backed, segment-tagged contact list.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Link href="/sources" className="no-underline">
          <Card className="hover:bg-neutral-100 transition cursor-pointer">
            <h3>Sources</h3>
            <p className="text-sm text-neutral-600 mt-2">
              Register URLs to crawl. Each source represents a directory or
              listing page (e.g. Northwind members, TTA directory).
            </p>
          </Card>
        </Link>
        <Link href="/runs" className="no-underline">
          <Card className="hover:bg-neutral-100 transition cursor-pointer">
            <h3>Runs</h3>
            <p className="text-sm text-neutral-600 mt-2">
              Launch a crawl/extract/dedupe pass with a budget cap. Watch
              progress and approve/reject contacts in the review queue.
            </p>
          </Card>
        </Link>
        <Link href="/review" className="no-underline">
          <Card className="hover:bg-neutral-100 transition cursor-pointer">
            <h3>Review queue</h3>
            <p className="text-sm text-neutral-600 mt-2">
              The contacts pending review across all runs. Filter by status,
              dedupe, has-email. Approve, reject, edit.
            </p>
          </Card>
        </Link>
        <Link href="/suppressions" className="no-underline">
          <Card className="hover:bg-neutral-100 transition cursor-pointer">
            <h3>Suppressions</h3>
            <p className="text-sm text-neutral-600 mt-2">
              Block-list of emails and domains. Blocks at dedupe and export.
            </p>
          </Card>
        </Link>
      </div>
    </div>
  );
}
