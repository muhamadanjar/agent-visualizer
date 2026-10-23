import { PageHeader, SampleNotice } from "../../components/workspace-components";
import { InboxList } from "../../components/inbox-list";

export default function InboxPage() {
  return <div className="page-stack"><PageHeader eyebrow="Owner attention" title="Inbox" description="Review sample input, approval, blocked work, failure, connection freshness, and capacity states."/><SampleNotice /><InboxList /></div>;
}
