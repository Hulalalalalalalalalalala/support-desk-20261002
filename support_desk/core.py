from .storage import JsonStore, text, minute, positive

PRIORITIES = ("urgent", "high", "normal", "low")
PRIORITY_RANK = {name: index for index, name in enumerate(PRIORITIES)}

_UNSET = object()


class _Edge:
    __slots__ = ("to", "rev", "cap")

    def __init__(self, to, rev, cap):
        self.to, self.rev, self.cap = to, rev, cap


def _route_coverage(candidates, rule_by_category, loads, limits):
    # Choose a globally feasible plan maximizing the number of assigned tickets.
    # Residual flow decides feasibility of each tentative decision; rerouting
    # through reverse edges lets an earlier ticket keep an undecided recipient.
    # limits maps every name in loads to its personal open-ticket ceiling.
    capacities = {name: limits[name] - load for name, load in loads.items()
                  if load < limits[name]}
    options = []
    for ticket in candidates:
        names = rule_by_category.get(ticket.get("category"))
        options.append(sorted(name for name in (names or ()) if name in capacities))
    n = len(candidates)

    def run(forced, free, caps):
        # Maximum flow with every ticket in forced carrying one unit (to any
        # eligible assignee); tickets in free stay optional. Returns the flow
        # value, or -1 when a forced ticket cannot be saturated.
        ticket_ids = sorted(set(forced) | set(free))
        if not ticket_ids:
            return 0
        assignee_ids = sorted({name for idx in ticket_ids for name in options[idx]
                               if caps.get(name, 0) > 0})
        ticket_node = {idx: 1 + pos for pos, idx in enumerate(ticket_ids)}
        assignee_node = {name: 1 + len(ticket_ids) + pos
                         for pos, name in enumerate(assignee_ids)}
        sink = 1 + len(ticket_ids) + len(assignee_ids)
        graph = [[] for _ in range(sink + 1)]

        def add_edge(head, tail, cap):
            graph[head].append(_Edge(tail, len(graph[tail]), cap))
            graph[tail].append(_Edge(head, len(graph[head]) - 1, 0))

        source_edges = {}
        for idx in ticket_ids:
            edge = _Edge(ticket_node[idx], len(graph[ticket_node[idx]]), 1)
            graph[0].append(edge)
            graph[ticket_node[idx]].append(_Edge(0, len(graph[0]) - 1, 0))
            source_edges[idx] = edge
            for name in options[idx]:
                if caps.get(name, 0) > 0:
                    add_edge(ticket_node[idx], assignee_node[name], 1)
        for name in assignee_ids:
            add_edge(assignee_node[name], sink, caps[name])

        total = 0

        def push_from(idx):
            # Find a residual path from this ticket node to the sink, then feed
            # it one unit from the source. Backward assignee->ticket edges may
            # reroute already matched tickets without dropping their unit.
            start = ticket_node[idx]
            parents = {start: None}
            queue = [start]
            for node in queue:
                for edge in graph[node]:
                    # Never climb back into the source: a forced ticket keeps its
                    # unit; only assignee->ticket reverse edges may reroute it.
                    if edge.cap > 0 and edge.to != 0 and edge.to not in parents:
                        parents[edge.to] = (node, edge)
                        queue.append(edge.to)
            if sink not in parents:
                return False
            node = sink
            while node != start:
                prev, edge = parents[node]
                edge.cap -= 1
                graph[node][edge.rev].cap += 1
                node = prev
            source_edges[idx].cap = 0
            graph[start][source_edges[idx].rev].cap = 1
            return True

        pending = list(forced)
        # Push forced tickets one at a time; a ticket that cannot be routed now
        # may succeed after another forced ticket triggers a reroute chain.
        while pending:
            stuck = True
            rest = []
            for idx in pending:
                if push_from(idx):
                    total += 1
                    stuck = False
                else:
                    rest.append(idx)
            if stuck:
                return -1
            pending = rest
        while True:
            parents = {0: None}
            queue = [0]
            for node in queue:
                for edge in graph[node]:
                    if edge.cap > 0 and edge.to not in parents:
                        parents[edge.to] = (node, edge)
                        queue.append(edge.to)
            if sink not in parents:
                break
            node = sink
            while node != 0:
                prev, edge = parents[node]
                edge.cap -= 1
                graph[node][edge.rev].cap += 1
                node = prev
            total += 1
        return total

    maximum = run([], range(n), capacities)
    bits = [False] * n
    for i in range(n):
        forced = [j for j in range(i) if bits[j]] + [i]
        if run(forced, range(i + 1, n), capacities) == maximum:
            bits[i] = True
    choices = [None] * n
    remaining_caps = dict(capacities)
    assigned_count = 0
    for i in range(n):
        if not bits[i]:
            continue
        for name in options[i]:
            if remaining_caps[name] <= 0:
                continue
            remaining_caps[name] -= 1
            required = maximum - assigned_count - 1
            forced = [j for j in range(i + 1, n) if bits[j]]
            if run(forced, [], remaining_caps) == required:
                choices[i] = name
                assigned_count += 1
                break
            remaining_caps[name] += 1
    return choices


def _service_periods(service_periods):
    if service_periods is None:
        return None
    if not isinstance(service_periods, list):
        raise ValueError("service_periods must be an array or null")
    intervals = []
    for period in service_periods:
        if not isinstance(period, list) or len(period) != 2:
            raise ValueError("each service period must be a [start, end] pair")
        start, end = period
        # bool is a subclass of int, so compare types explicitly.
        if type(start) is not int or type(end) is not int:
            raise ValueError("service period endpoints must be nonnegative integer minutes")
        if start < 0 or end < 0 or start >= end:
            raise ValueError("service period endpoints must be nonnegative with start earlier than end")
        intervals.append((start, end))
    # Merge overlapping, repeated and touching periods: covered minutes are
    # counted once and input order never changes the result.
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _service_minutes(periods, start, end):
    # Half-open intervals: minutes at start count, the minute at end does not.
    total = 0
    for left, right in periods:
        if right <= start:
            continue
        if left >= end:
            break
        total += min(right, end) - max(left, start)
    return total


def _due_minute(periods, opened_at, target):
    # Earliest simulated minute m at which the covered minutes from opened_at
    # through m-1 first reach target; periods are merged half-open intervals,
    # so the end of a period is a valid answer. None means the given periods can
    # never accumulate the target, and an empty period list never reaches it.
    accumulated = 0
    for left, right in periods:
        if right <= opened_at:
            continue
        start = left if left > opened_at else opened_at
        covered = right - start
        if accumulated + covered >= target:
            return start + target - accumulated
        accumulated += covered
    return None


class SupportDesk(JsonStore):
    def open_ticket(self, ticket_id, customer, subject, opened_at=None):
        ticket_id, customer, subject = text(ticket_id, "ticket_id"), text(customer, "customer"), text(subject, "subject")
        if opened_at is not None:
            opened_at = minute(opened_at, "opened_at")
        data = self._read()
        tickets = data.setdefault("tickets", {})
        if ticket_id in tickets:
            raise ValueError("ticket already exists")
        ticket = {"ticket_id": ticket_id, "customer": customer, "subject": subject, "status": "open", "assignee": None, "notes": [], "resolution": None}
        if opened_at is not None:
            ticket["opened_at"] = opened_at
            ticket["first_response"] = None
        tickets[ticket_id] = ticket
        self._write(data)
        return ticket

    def get(self, ticket_id):
        ticket = self._read().get("tickets", {}).get(ticket_id)
        if ticket is None:
            raise ValueError("unknown ticket")
        return ticket

    def _change(self, ticket_id, operation):
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        operation(ticket)
        self._write(data)
        return ticket

    def assign(self, ticket_id, assignee):
        assignee = text(assignee, "assignee")
        return self._change(ticket_id, lambda t: t.update(assignee=assignee))

    def transfer_ticket(self, ticket_id, assignee, reason, transferred_at):
        ticket_id, assignee, reason = (text(ticket_id, "ticket_id"),
                                       text(assignee, "assignee"),
                                       text(reason, "reason"))
        transferred_at = minute(transferred_at, "transferred_at")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "open" or not ticket.get("assignee"):
            raise ValueError("ticket must exist, be open and assigned")
        if assignee == ticket["assignee"]:
            raise ValueError("assignee must differ from the current assignee")
        if "opened_at" in ticket and transferred_at < ticket["opened_at"]:
            raise ValueError("transferred_at must not be earlier than opened_at")
        history = ticket.get("transfer_history")
        if history and transferred_at < history[-1]["transferred_at"]:
            raise ValueError("transferred_at must not be earlier than the last transfer")
        history = ticket.setdefault("transfer_history", [])
        history.append({"from_assignee": ticket["assignee"], "to_assignee": assignee,
                        "reason": reason, "transferred_at": transferred_at})
        ticket["assignee"] = assignee
        self._write(data)
        return ticket

    def handover(self, source_assignee, assignees, reason, transferred_at, max_open=5):
        source_assignee = text(source_assignee, "source_assignee")
        reason = text(reason, "reason")
        if not isinstance(assignees, list) or not assignees:
            raise ValueError("assignees must be a nonempty array")
        names = []
        for element in assignees:
            if not isinstance(element, str) or not element.strip():
                raise ValueError("assignees elements must be nonblank strings")
            names.append(element.strip())
        if len(set(names)) != len(names):
            raise ValueError("assignees must not contain duplicate names")
        if source_assignee in names:
            raise ValueError("assignees must not contain the source assignee")
        transferred_at = minute(transferred_at, "transferred_at")
        max_open = positive(max_open, "max_open")
        data = self._read()
        tickets = data.get("tickets", {})
        def candidate_key(ticket):
            opened_at = ticket.get("opened_at")
            return (PRIORITY_RANK[ticket.get("priority", "normal")],
                    opened_at is None, opened_at if opened_at is not None else 0,
                    ticket["ticket_id"])
        selected = sorted((ticket for ticket in tickets.values()
                           if ticket["status"] == "open" and ticket.get("assignee") == source_assignee),
                          key=candidate_key)
        if not selected:
            return []
        loads = {name: 0 for name in names}
        for ticket in tickets.values():
            if ticket["status"] == "open" and ticket.get("assignee") in loads:
                loads[ticket["assignee"]] += 1
        plan = []
        for ticket in selected:
            available = [name for name in names if loads[name] < max_open]
            if not available:
                raise ValueError("assignees do not have enough capacity for all selected tickets")
            chosen = min(available, key=lambda name: (loads[name], name))
            loads[chosen] += 1
            plan.append((ticket, chosen))
        for ticket, chosen in plan:
            if "opened_at" in ticket and transferred_at < ticket["opened_at"]:
                raise ValueError("transferred_at must not be earlier than opened_at")
            history = ticket.get("transfer_history")
            if history and transferred_at < history[-1]["transferred_at"]:
                raise ValueError("transferred_at must not be earlier than the last transfer")
        handed_over = []
        for ticket, chosen in plan:
            ticket.setdefault("transfer_history", []).append(
                {"from_assignee": ticket["assignee"], "to_assignee": chosen,
                 "reason": reason, "transferred_at": transferred_at})
            ticket["assignee"] = chosen
            handed_over.append(ticket)
        self._write(data)
        return handed_over

    def note(self, ticket_id, message):
        message = text(message, "message")
        return self._change(ticket_id, lambda t: t["notes"].append(message))

    @staticmethod
    def _earlier_close_times(ticket):
        # Times a closure must not be earlier than; histories missing on old
        # tickets count as no records and are never backfilled.
        earlier = []
        if "opened_at" in ticket:
            earlier.append(ticket["opened_at"])
        response = ticket.get("first_response")
        if isinstance(response, dict) and response.get("responded_at") is not None:
            earlier.append(response["responded_at"])
        earlier.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
        earlier.extend(message["received_at"] for message in ticket.get("customer_messages") or [])
        earlier.extend(entry["transferred_at"] for entry in ticket.get("transfer_history") or [])
        earlier.extend(entry["closed_at"] for entry in ticket.get("reopen_history") or []
                       if "closed_at" in entry)
        return earlier

    def close(self, ticket_id, resolution, closed_at=None):
        resolution = text(resolution, "resolution")
        if closed_at is not None:
            closed_at = minute(closed_at, "closed_at")
        def apply(ticket):
            if not ticket["assignee"]:
                raise ValueError("assign the ticket before closing")
            if closed_at is not None:
                earlier = self._earlier_close_times(ticket)
                if earlier and closed_at < max(earlier):
                    raise ValueError("closed_at must not be earlier than existing ticket times")
            ticket.update(status="closed", resolution=resolution)
            if closed_at is not None:
                ticket["closed_at"] = closed_at
        return self._change(ticket_id, apply)

    def close_many(self, items):
        if not isinstance(items, list) or not items:
            raise ValueError("items must be a nonempty array")
        normalized = []
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not {"ticket_id", "resolution"} <= set(item) \
                    or not set(item) <= {"ticket_id", "resolution", "closed_at"}:
                raise ValueError("each item must contain only ticket_id, resolution and optional closed_at")
            ticket_id = text(item["ticket_id"], "ticket_id")
            resolution = text(item["resolution"], "resolution")
            closed_at = item.get("closed_at")
            if closed_at is not None:
                closed_at = minute(closed_at, "closed_at")
            if ticket_id in seen:
                raise ValueError("items must not contain duplicate ticket ids")
            seen.add(ticket_id)
            normalized.append((ticket_id, resolution, closed_at))
        data = self._read()
        tickets = data.get("tickets", {})
        targets = []
        # Validate every selected ticket before mutating any, so a rejected
        # batch leaves all tickets open and writes no directory or file.
        for ticket_id, resolution, closed_at in normalized:
            ticket = tickets.get(ticket_id)
            if ticket is None or ticket["status"] != "open" or not ticket.get("assignee"):
                raise ValueError("ticket must exist, be open and assigned")
            if closed_at is not None:
                earlier = self._earlier_close_times(ticket)
                if earlier and closed_at < max(earlier):
                    raise ValueError("closed_at must not be earlier than existing ticket times")
            targets.append((ticket, resolution, closed_at))
        closed = []
        for ticket, resolution, closed_at in targets:
            ticket.update(status="closed", resolution=resolution)
            if closed_at is not None:
                ticket["closed_at"] = closed_at
            closed.append(ticket)
        self._write(data)
        return closed

    def reopen_ticket(self, ticket_id, reason):
        ticket_id, reason = text(ticket_id, "ticket_id"), text(reason, "reason")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "closed":
            raise ValueError("ticket must exist and be closed")
        record = {"reason": reason, "resolution": ticket["resolution"]}
        if "closed_at" in ticket:
            record["closed_at"] = ticket.pop("closed_at")
        ticket.setdefault("reopen_history", []).append(record)
        ticket.update(status="open", resolution=None)
        self._write(data)
        return ticket

    def priority_queue(self):
        items = [{"ticket": ticket, "priority": ticket.get("priority", "normal")}
                 for ticket in self._read().get("tickets", {}).values()
                 if ticket["status"] != "closed"]
        items.sort(key=lambda item: (PRIORITY_RANK[item["priority"]], item["ticket"]["ticket_id"]))
        return items

    def set_priority(self, ticket_id, priority):
        ticket_id, priority = text(ticket_id, "ticket_id"), text(priority, "priority")
        if priority not in PRIORITY_RANK:
            raise ValueError("priority must be one of low, normal, high, urgent")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        ticket["priority"] = priority
        self._write(data)
        return ticket

    def escalate_overdue(self, as_of, response_minutes=30, customer_minutes=30):
        as_of = minute(as_of, "as_of")
        response_minutes = positive(response_minutes, "response_minutes")
        customer_minutes = positive(customer_minutes, "customer_minutes")
        data = self._read()
        selected = []
        # All time validation runs before any priority change, so a future
        # record rejects the whole call without leaving partial escalations.
        for ticket in data.get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            response = ticket.get("first_response")
            has_response = isinstance(response, dict) and bool(response)
            if not has_response and "opened_at" in ticket:
                if ticket["opened_at"] > as_of:
                    raise ValueError("opened_at must not be later than as_of")
            messages = ticket.get("customer_messages")
            if messages:
                answer_times = ([response["responded_at"]] if has_response else [])
                answer_times.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
                if any(message["received_at"] > as_of for message in messages) or \
                        any(time > as_of for time in answer_times):
                    raise ValueError("customer message or response time must not be later than as_of")
            overdue = False
            if not has_response:
                # Missing, null or an empty object means the ticket still awaits
                # its first response; tickets without opened_at do not take part.
                if "opened_at" in ticket and as_of - ticket["opened_at"] > response_minutes:
                    overdue = True
            if not overdue and messages:
                # The answer boundary is the latest of the first response and all
                # later replies; a follow-up in the same minute is already covered.
                boundary = max(answer_times) if answer_times else None
                unanswered = [message["received_at"] for message in messages
                              if boundary is None or message["received_at"] > boundary]
                if unanswered and as_of - min(unanswered) > customer_minutes:
                    overdue = True
            if overdue and ticket.get("priority") != "urgent":
                selected.append(ticket)
        selected.sort(key=lambda ticket: ticket["ticket_id"])
        for ticket in selected:
            ticket["priority"] = "urgent"
        if selected:
            self._write(data)
        return selected

    def respond(self, ticket_id, message, responded_at):
        ticket_id, message = text(ticket_id, "ticket_id"), text(message, "message")
        responded_at = minute(responded_at, "responded_at")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if "opened_at" not in ticket:
            raise ValueError("ticket has no opened_at")
        response = ticket.get("first_response")
        # Missing, null or an empty object means the ticket still awaits its
        # first response; only a non-empty object blocks a new registration.
        if isinstance(response, dict) and response:
            raise ValueError("ticket already has a first response")
        if responded_at < ticket["opened_at"]:
            raise ValueError("responded_at must not be earlier than opened_at")
        ticket["first_response"] = {"message": message, "responded_at": responded_at}
        self._write(data)
        return ticket

    def _knowledge_snapshot(self, data, article_id, revision):
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        if not data.get("knowledge_enabled", {}).get(article_id, True):
            raise ValueError("knowledge article is disabled")
        if revision is None:
            return dict(entry), None
        history = data.get("knowledge_history", {}).get(article_id)
        if history is None:
            # Articles written before history existed keep their current content as revision 1;
            # the missing history is never reconstructed or backfilled.
            source = ({"revision": 1, "title": entry["title"], "content": entry["content"]}
                      if revision == 1 else None)
        else:
            source = next((item for item in history if item["revision"] == revision), None)
        if source is None:
            raise ValueError("unknown knowledge revision")
        snapshot = {"article_id": entry["article_id"], "source_ticket_id": entry["source_ticket_id"],
                    "title": source["title"], "content": source["content"]}
        return snapshot, revision

    def respond_with_knowledge(self, ticket_id, article_id, responded_at=None, revision=None):
        ticket_id, article_id = text(ticket_id, "ticket_id"), text(article_id, "article_id")
        responded_at = minute(responded_at, "responded_at")
        if revision is not None:
            revision = positive(revision, "revision")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if "opened_at" not in ticket:
            raise ValueError("ticket has no opened_at")
        response = ticket.get("first_response")
        # Missing, null or an empty object means the ticket still awaits its
        # first response; only a non-empty object blocks a new registration.
        if isinstance(response, dict) and response:
            raise ValueError("ticket already has a first response")
        snapshot, chosen_revision = self._knowledge_snapshot(data, article_id, revision)
        if responded_at < ticket["opened_at"]:
            raise ValueError("responded_at must not be earlier than opened_at")
        record = {
            "message": snapshot["content"],
            "responded_at": responded_at,
            "knowledge": snapshot,
        }
        if chosen_revision is not None:
            record["knowledge_revision"] = chosen_revision
        ticket["first_response"] = record
        self._write(data)
        return ticket

    def respond_many(self, items):
        # Register first responses for an explicitly selected set of tickets in
        # one atomic batch: all of them are saved together, or validation leaves
        # every ticket, the store directory and the data file untouched.
        if not isinstance(items, list) or not items:
            raise ValueError("items must be a nonempty array")
        allowed = {"ticket_id", "responded_at", "message", "article_id", "revision"}
        normalized = []
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not {"ticket_id", "responded_at"} <= set(item) \
                    or not set(item) <= allowed \
                    or ("message" in item) == ("article_id" in item):
                # Exactly one of message and article_id must be present; revision
                # may only accompany a knowledge reference, so any other key (or
                # neither/both bodies) rejects the whole group.
                raise ValueError("each item must contain ticket_id, responded_at and exactly one of message or article_id")
            ticket_id = text(item["ticket_id"], "ticket_id")
            responded_at = minute(item["responded_at"], "responded_at")
            if ticket_id in seen:
                raise ValueError("items must not contain duplicate ticket ids")
            seen.add(ticket_id)
            if "message" in item:
                if "revision" in item:
                    raise ValueError("revision is allowed only with article_id")
                body = text(item["message"], "message")
                normalized.append((ticket_id, responded_at, ("manual", body)))
            else:
                article_id = text(item["article_id"], "article_id")
                raw_revision = item.get("revision")
                revision = None if raw_revision is None else positive(raw_revision, "revision")
                normalized.append((ticket_id, responded_at, ("knowledge", article_id, revision)))
        data = self._read()
        tickets = data.get("tickets", {})
        targets = []
        # Resolve and validate every selected ticket and knowledge reference
        # before mutating anything, so a rejected batch writes no directory or
        # file and leaves no partial first response (e.g. a disabled article on
        # the last item keeps every earlier ticket unanswered).
        for ticket_id, responded_at, choice in normalized:
            ticket = tickets.get(ticket_id)
            if ticket is None or ticket["status"] == "closed":
                raise ValueError("ticket must exist and be open")
            if "opened_at" not in ticket:
                raise ValueError("ticket has no opened_at")
            response = ticket.get("first_response")
            # Missing, null or an empty object means the ticket still awaits its
            # first response; only a non-empty object blocks the batch.
            if isinstance(response, dict) and response:
                raise ValueError("ticket already has a first response")
            if responded_at < ticket["opened_at"]:
                raise ValueError("responded_at must not be earlier than opened_at")
            if choice[0] == "manual":
                record = {"message": choice[1], "responded_at": responded_at}
            else:
                snapshot, chosen_revision = self._knowledge_snapshot(data, choice[1], choice[2])
                record = {
                    "message": snapshot["content"],
                    "responded_at": responded_at,
                    "knowledge": snapshot,
                }
                if chosen_revision is not None:
                    record["knowledge_revision"] = chosen_revision
            targets.append((ticket, record))
        responded = []
        for ticket, record in targets:
            ticket["first_response"] = record
            responded.append(ticket)
        self._write(data)
        return responded

    def _reply_target(self, data, ticket_id):
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if "opened_at" not in ticket:
            raise ValueError("ticket has no opened_at")
        response = ticket.get("first_response")
        # Missing, null or an empty object means there is no first response.
        if not isinstance(response, dict) or not response:
            raise ValueError("ticket has no first response")
        return ticket

    def _check_reply_time(self, ticket, replied_at):
        if replied_at < ticket["first_response"]["responded_at"]:
            raise ValueError("replied_at must not be earlier than the first response")
        replies = ticket.get("replies")
        if replies and replied_at < replies[-1]["replied_at"]:
            raise ValueError("replied_at must not be earlier than the last reply")

    def reply(self, ticket_id, message, replied_at):
        ticket_id, message = text(ticket_id, "ticket_id"), text(message, "message")
        replied_at = minute(replied_at, "replied_at")
        data = self._read()
        ticket = self._reply_target(data, ticket_id)
        self._check_reply_time(ticket, replied_at)
        ticket.setdefault("replies", []).append({"message": message, "replied_at": replied_at})
        self._write(data)
        return ticket

    def reply_with_knowledge(self, ticket_id, article_id, replied_at, revision=None):
        ticket_id, article_id = text(ticket_id, "ticket_id"), text(article_id, "article_id")
        replied_at = minute(replied_at, "replied_at")
        if revision is not None:
            revision = positive(revision, "revision")
        data = self._read()
        ticket = self._reply_target(data, ticket_id)
        snapshot, chosen_revision = self._knowledge_snapshot(data, article_id, revision)
        self._check_reply_time(ticket, replied_at)
        record = {
            "message": snapshot["content"],
            "replied_at": replied_at,
            "knowledge": snapshot,
        }
        if chosen_revision is not None:
            record["knowledge_revision"] = chosen_revision
        ticket.setdefault("replies", []).append(record)
        self._write(data)
        return ticket

    def reply_many(self, items):
        # Append follow-up replies for an explicitly selected set of tickets in
        # one atomic batch: every reply is saved together, or validation leaves
        # every ticket, the store directory and the data file untouched.
        if not isinstance(items, list) or not items:
            raise ValueError("items must be a nonempty array")
        allowed = {"ticket_id", "replied_at", "message", "article_id", "revision"}
        normalized = []
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not {"ticket_id", "replied_at"} <= set(item) \
                    or not set(item) <= allowed \
                    or ("message" in item) == ("article_id" in item):
                # Exactly one of message and article_id must be present; revision
                # may only accompany a knowledge reference, so any other key (or
                # neither/both bodies) rejects the whole group.
                raise ValueError("each item must contain ticket_id, replied_at and exactly one of message or article_id")
            ticket_id = text(item["ticket_id"], "ticket_id")
            replied_at = minute(item["replied_at"], "replied_at")
            if ticket_id in seen:
                raise ValueError("items must not contain duplicate ticket ids")
            seen.add(ticket_id)
            if "message" in item:
                if "revision" in item:
                    raise ValueError("revision is allowed only with article_id")
                body = text(item["message"], "message")
                normalized.append((ticket_id, replied_at, ("manual", body)))
            else:
                article_id = text(item["article_id"], "article_id")
                raw_revision = item.get("revision")
                revision = None if raw_revision is None else positive(raw_revision, "revision")
                normalized.append((ticket_id, replied_at, ("knowledge", article_id, revision)))
        data = self._read()
        tickets = data.get("tickets", {})
        targets = []
        # Resolve and validate every selected ticket, time and knowledge
        # reference before mutating anything, so a rejected batch writes no
        # directory or file and leaves no partial reply (e.g. a disabled article
        # on the last item keeps every earlier ticket unchanged).
        for ticket_id, replied_at, choice in normalized:
            ticket = self._reply_target(data, ticket_id)
            if choice[0] == "manual":
                self._check_reply_time(ticket, replied_at)
                record = {"message": choice[1], "replied_at": replied_at}
            else:
                snapshot, chosen_revision = self._knowledge_snapshot(data, choice[1], choice[2])
                self._check_reply_time(ticket, replied_at)
                record = {
                    "message": snapshot["content"],
                    "replied_at": replied_at,
                    "knowledge": snapshot,
                }
                if chosen_revision is not None:
                    record["knowledge_revision"] = chosen_revision
            targets.append((ticket, record))
        replied = []
        for ticket, record in targets:
            ticket.setdefault("replies", []).append(record)
            replied.append(ticket)
        self._write(data)
        return replied

    def receive(self, ticket_id, message, received_at):
        ticket_id, message = text(ticket_id, "ticket_id"), text(message, "message")
        received_at = minute(received_at, "received_at")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "open" or "opened_at" not in ticket:
            raise ValueError("ticket must exist, be open and have opened_at")
        if received_at < ticket["opened_at"]:
            raise ValueError("received_at must not be earlier than opened_at")
        messages = ticket.get("customer_messages")
        if messages and received_at < messages[-1]["received_at"]:
            raise ValueError("received_at must not be earlier than the last customer message")
        ticket.setdefault("customer_messages", []).append(
            {"message": message, "received_at": received_at})
        self._write(data)
        return ticket

    def receive_once(self, ticket_id, message, received_at, request_id):
        # Idempotent follow-up registration keyed by a client request id; the
        # request association lives beside the ticket in root/data.json, so a
        # recreated SupportDesk still recognizes retries.
        ticket_id, message, request_id = (text(ticket_id, "ticket_id"),
                                          text(message, "message"),
                                          text(request_id, "request_id"))
        received_at = minute(received_at, "received_at")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        requests = ticket.get("customer_requests") if ticket is not None else None
        if requests is not None and request_id in requests:
            # A retry is recognized in any ticket state: closed, reopened, with
            # later follow-ups or answers, it never appends again and never writes.
            saved = requests[request_id]
            if saved["message"] != message or saved["received_at"] != received_at:
                raise ValueError("request_id was already used with a different message or received_at")
            return {"ticket": ticket, "created": False}
        # First use of the id follows the same ticket and time rules as receive;
        # old follow-ups and plain receive records never reserve a request id.
        if ticket is None or ticket["status"] != "open" or "opened_at" not in ticket:
            raise ValueError("ticket must exist, be open and have opened_at")
        if received_at < ticket["opened_at"]:
            raise ValueError("received_at must not be earlier than opened_at")
        messages = ticket.get("customer_messages")
        if messages and received_at < messages[-1]["received_at"]:
            raise ValueError("received_at must not be earlier than the last customer message")
        ticket.setdefault("customer_messages", []).append(
            {"message": message, "received_at": received_at})
        ticket.setdefault("customer_requests", {})[request_id] = \
            {"message": message, "received_at": received_at}
        self._write(data)
        return {"ticket": ticket, "created": True}

    def response_stats(self):
        tickets = list(self._read().get("tickets", {}).values())
        timed = [t for t in tickets if "opened_at" in t]
        # Missing, null or an empty object means the ticket is still pending.
        responded = [t for t in timed
                     if isinstance(t.get("first_response"), dict) and t["first_response"]]
        durations = [t["first_response"]["responded_at"] - t["opened_at"] for t in responded]
        return {
            "timed": len(timed),
            "responded": len(responded),
            "pending": len(timed) - len(responded),
            "untimed": len(tickets) - len(timed),
            "average_minutes": sum(durations) / len(durations) if durations else None,
            "max_minutes": max(durations) if durations else None,
        }

    def closure_report(self, as_of):
        as_of = minute(as_of, "as_of")
        closed = [ticket for ticket in self._read().get("tickets", {}).values()
                  if ticket["status"] == "closed"]
        for ticket in closed:
            opened_at = ticket.get("opened_at")
            if opened_at is not None and opened_at > as_of:
                raise ValueError("opened_at must not be later than as_of")
            closed_at = ticket.get("closed_at")
            if closed_at is not None and closed_at > as_of:
                raise ValueError("closed_at must not be later than as_of")
        durations = [ticket["closed_at"] - ticket["opened_at"] for ticket in closed
                     if "opened_at" in ticket and "closed_at" in ticket]
        return {
            "as_of": as_of,
            "total": len(closed),
            "timed": len(durations),
            "untimed": len(closed) - len(durations),
            "average_minutes": sum(durations) / len(durations) if durations else None,
            "max_minutes": max(durations) if durations else None,
        }

    def response_queue(self, as_of, target_minutes=30):
        as_of = minute(as_of, "as_of")
        target_minutes = positive(target_minutes, "target_minutes")
        items = []
        untimed = 0
        for ticket in self._read().get("tickets", {}).values():
            response = ticket.get("first_response")
            # Missing, null or an empty object means the ticket still awaits
            # its first response and stays in the queue.
            if ticket["status"] == "closed" or (isinstance(response, dict) and response):
                continue
            if "opened_at" not in ticket:
                untimed += 1
                continue
            if ticket["opened_at"] > as_of:
                raise ValueError("opened_at must not be later than as_of")
            waiting = as_of - ticket["opened_at"]
            items.append({"ticket": ticket, "waiting_minutes": waiting, "overdue": waiting > target_minutes})
        items.sort(key=lambda item: (item["ticket"]["opened_at"], item["ticket"]["ticket_id"]))
        return {"untimed": untimed, "items": items}

    def follow_up_queue(self, as_of, target_minutes=60):
        as_of = minute(as_of, "as_of")
        target_minutes = positive(target_minutes, "target_minutes")
        entries = []
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            response = ticket.get("first_response")
            # Only a non-empty first-response object qualifies; missing, null or {} are excluded.
            if not isinstance(response, dict) or not response:
                continue
            replies = ticket.get("replies")
            if replies:
                last_answered_at = replies[-1]["replied_at"]
            else:
                last_answered_at = response["responded_at"]
            if response["responded_at"] > as_of or any(reply["replied_at"] > as_of for reply in replies or []):
                raise ValueError("response time must not be later than as_of")
            entries.append((ticket, ticket.get("priority", "normal"), last_answered_at))
        items = []
        for ticket, priority, last_answered_at in entries:
            waiting = as_of - last_answered_at
            items.append({"ticket": ticket, "priority": priority, "last_answered_at": last_answered_at,
                          "waiting_minutes": waiting, "overdue": waiting > target_minutes})
        items.sort(key=lambda item: (item["last_answered_at"], PRIORITY_RANK[item["priority"]],
                                     item["ticket"]["ticket_id"]))
        return {"as_of": as_of, "items": items}

    def customer_queue(self, as_of, target_minutes=30):
        as_of = minute(as_of, "as_of")
        target_minutes = positive(target_minutes, "target_minutes")
        entries = []
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            messages = ticket.get("customer_messages")
            # Only open tickets with a follow-up history participate.
            if not messages:
                continue
            response = ticket.get("first_response")
            # Missing, null or an empty object means there is no first response.
            response_at = response["responded_at"] if isinstance(response, dict) and response else None
            answer_times = ([response_at] if response_at is not None else [])
            answer_times.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
            # Any follow-up or answer later than as_of invalidates the whole query.
            if any(message["received_at"] > as_of for message in messages) or \
                    any(time > as_of for time in answer_times):
                raise ValueError("customer message or response time must not be later than as_of")
            boundary = max(answer_times) if answer_times else None
            # A follow-up in the same minute as an answer is already covered;
            # only strictly later times remain unanswered.
            unanswered = [message for message in messages
                          if boundary is None or message["received_at"] > boundary]
            if not unanswered:
                continue
            earliest = min(message["received_at"] for message in unanswered)
            waiting = as_of - earliest
            entries.append((earliest, ticket, len(unanswered), waiting))
        entries.sort(key=lambda entry: (entry[0], entry[1]["ticket_id"]))
        return [{"ticket": ticket, "count": count, "waiting_minutes": waiting,
                 "overdue": waiting > target_minutes}
                for earliest, ticket, count, waiting in entries]

    def pending_queue(self, as_of, response_minutes=30, customer_minutes=30):
        as_of = minute(as_of, "as_of")
        response_minutes = positive(response_minutes, "response_minutes")
        customer_minutes = positive(customer_minutes, "customer_minutes")
        entries = []
        untimed = 0
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] != "open":
                # Closed tickets leave the queue and skip every time check.
                continue
            response = ticket.get("first_response")
            # Missing, null or an empty object means the ticket still awaits its first response.
            has_response = isinstance(response, dict) and bool(response)
            messages = ticket.get("customer_messages")
            if not has_response:
                if "opened_at" not in ticket:
                    # No registration time: counted as untimed and produces no response wait.
                    untimed += 1
                elif ticket["opened_at"] > as_of:
                    raise ValueError("opened_at must not be later than as_of")
            if messages:
                answer_times = ([response["responded_at"]] if has_response else [])
                answer_times.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
                # On any ticket with a follow-up history, a follow-up, first response or
                # later reply later than as_of invalidates the whole query even when every
                # follow-up is already covered by an answer.
                if any(message["received_at"] > as_of for message in messages) or \
                        any(time > as_of for time in answer_times):
                    raise ValueError("customer message or response time must not be later than as_of")
            else:
                answer_times = []
            response_wait = None
            if not has_response and "opened_at" in ticket:
                response_wait = as_of - ticket["opened_at"]
            customer_wait = None
            count = 0
            if messages:
                # The answer boundary is the latest of the first response and all later
                # replies; with no answer every follow-up stays unanswered. A follow-up in
                # the same minute as the boundary is already covered; duplicate follow-ups
                # are counted separately.
                boundary = max(answer_times) if answer_times else None
                unanswered = [message["received_at"] for message in messages
                              if boundary is None or message["received_at"] > boundary]
                count = len(unanswered)
                if unanswered:
                    customer_wait = as_of - min(unanswered)
            # A ticket with neither kind of pending wait never enters the queue.
            if response_wait is None and customer_wait is None:
                continue
            overdue = (response_wait is not None and response_wait > response_minutes) or \
                      (customer_wait is not None and customer_wait > customer_minutes)
            largest_wait = max(wait for wait in (response_wait, customer_wait) if wait is not None)
            entries.append((ticket, response_wait, customer_wait, count, overdue, largest_wait))
        # Overdue tickets first, then urgent/high/normal/low (missing priority is normal),
        # then the larger non-null wait descending, finally the case-sensitive ticket id.
        entries.sort(key=lambda entry: (not entry[4],
                                        PRIORITY_RANK[entry[0].get("priority", "normal")],
                                        -entry[5], entry[0]["ticket_id"]))
        return {"untimed": untimed,
                "items": [{"ticket": ticket, "response_wait": response_wait,
                           "customer_wait": customer_wait, "count": count, "overdue": overdue}
                          for ticket, response_wait, customer_wait, count, overdue, _ in entries]}

    def customer_response_report(self, as_of, since=0, until=None):
        # Parameters are validated even when the store holds no data.
        as_of = minute(as_of, "as_of")
        since = minute(since, "since")
        if until is not None:
            until = minute(until, "until")
            if until < since:
                raise ValueError("until must not be earlier than since")
        tickets = list(self._read().get("tickets", {}).values())
        # Only tickets with a follow-up history can contribute; open and closed tickets alike.
        participants = [ticket for ticket in tickets if ticket.get("customer_messages")]
        answers = {}
        windowed = {}
        for ticket in participants:
            # The window selects follow-ups by received time only: since is
            # inclusive, until exclusive and omitted/null means no upper bound;
            # equal bounds make the interval empty.
            pairs = [(index, message) for index, message in enumerate(ticket["customer_messages"])
                     if message["received_at"] >= since
                     and (until is None or message["received_at"] < until)]
            # A ticket without a selected follow-up takes no part in the query.
            if not pairs:
                continue
            response = ticket.get("first_response")
            # Missing, null or an empty object means there is no first response;
            # handwritten and knowledge answers are collected the same way. The
            # answer set ignores the window: an answer outside it may still cover
            # a selected follow-up.
            times = [response["responded_at"]] if isinstance(response, dict) and response else []
            times.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
            # A selected follow-up, or any answer of the same ticket, later than
            # as_of invalidates the whole query; follow-ups outside the window
            # and tickets without a selected follow-up are never checked.
            if any(message["received_at"] > as_of for _, message in pairs) or \
                    any(time > as_of for time in times):
                raise ValueError("customer message or response time must not be later than as_of")
            answers[ticket["ticket_id"]] = times
            windowed[ticket["ticket_id"]] = pairs
        items = []
        for ticket in participants:
            ticket_id = ticket["ticket_id"]
            pairs = windowed.get(ticket_id)
            if not pairs:
                continue
            for index, message in pairs:
                received_at = message["received_at"]
                # Matching looks at time only: the earliest answer not earlier than
                # the follow-up covers it, regardless of call order or window bounds;
                # the same answer may cover several follow-ups and equal times take
                # zero minutes.
                candidates = [time for time in answers[ticket_id] if time >= received_at]
                if candidates:
                    answered_at = min(candidates)
                    items.append({"ticket_id": ticket_id, "index": index,
                                  "received_at": received_at, "answered_at": answered_at,
                                  "response_minutes": answered_at - received_at,
                                  "outcome": "answered"})
                else:
                    outcome = "pending" if ticket["status"] != "closed" else "closed_without_answer"
                    items.append({"ticket_id": ticket_id, "index": index,
                                  "received_at": received_at, "answered_at": None,
                                  "response_minutes": None, "outcome": outcome})
        items.sort(key=lambda item: (item["received_at"], item["ticket_id"], item["index"]))
        durations = [item["response_minutes"] for item in items if item["outcome"] == "answered"]
        summary = {
            "total": len(items),
            "answered": sum(1 for item in items if item["outcome"] == "answered"),
            "pending": sum(1 for item in items if item["outcome"] == "pending"),
            "closed_without_answer": sum(1 for item in items
                                         if item["outcome"] == "closed_without_answer"),
            "average_minutes": sum(durations) / len(durations) if durations else None,
            "max_minutes": max(durations) if durations else None,
        }
        return {"as_of": as_of, "summary": summary, "items": items}

    def customer_response_target_report(self, as_of, since=0, until=None, targets=None,
                                        service_periods=None):
        # Parameters are validated even when the store holds no data.
        as_of = minute(as_of, "as_of")
        since = minute(since, "since")
        if until is not None:
            until = minute(until, "until")
            if until < since:
                raise ValueError("until must not be earlier than since")
        target_minutes = {"urgent": 5, "high": 15, "normal": 30, "low": 60}
        if targets is not None:
            if not isinstance(targets, dict):
                raise ValueError("targets must be an object or null")
            for priority, value in targets.items():
                if priority not in PRIORITY_RANK:
                    raise ValueError("targets has an unknown priority: " + str(priority))
                target_minutes[priority] = positive(value, "targets." + priority)
        periods = _service_periods(service_periods)
        tickets = list(self._read().get("tickets", {}).values())
        # Only tickets with a follow-up history can contribute; open and closed tickets alike.
        participants = [ticket for ticket in tickets if ticket.get("customer_messages")]
        answers = {}
        windowed = {}
        for ticket in participants:
            # The window selects follow-ups by received time only: since is
            # inclusive, until exclusive and omitted/null means no upper bound;
            # equal bounds make the interval empty.
            pairs = [(index, message) for index, message in enumerate(ticket["customer_messages"])
                     if message["received_at"] >= since
                     and (until is None or message["received_at"] < until)]
            # A ticket without a selected follow-up takes no part in the query.
            if not pairs:
                continue
            response = ticket.get("first_response")
            # Missing, null or an empty object means there is no first response;
            # handwritten and knowledge answers are collected the same way. The
            # answer set ignores the window: an answer outside it may still cover
            # a selected follow-up.
            times = [response["responded_at"]] if isinstance(response, dict) and response else []
            times.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
            # A selected follow-up, or any answer of the same ticket, later than
            # as_of invalidates the whole query; follow-ups outside the window
            # and tickets without a selected follow-up are never checked. Empty
            # service periods do not waive this check.
            if any(message["received_at"] > as_of for _, message in pairs) or \
                    any(time > as_of for time in times):
                raise ValueError("customer message or response time must not be later than as_of")
            answers[ticket["ticket_id"]] = times
            windowed[ticket["ticket_id"]] = pairs
        # One entry per selected follow-up; duplicate follow-up records count
        # independently and one answer may cover several of them.
        entries = []
        for ticket in participants:
            ticket_id = ticket["ticket_id"]
            pairs = windowed.get(ticket_id)
            if not pairs:
                continue
            priority = ticket.get("priority", "normal")
            for index, message in pairs:
                received_at = message["received_at"]
                # Matching looks at time only: the earliest answer not earlier than
                # the follow-up covers it, regardless of call order or window bounds;
                # equal times take zero minutes.
                candidates = [time for time in answers[ticket_id] if time >= received_at]
                if candidates:
                    answered_at = min(candidates)
                    if periods is None:
                        elapsed = answered_at - received_at
                    else:
                        # Only minutes covered by a service period count; periods
                        # before receipt or after the answer contribute nothing.
                        elapsed = _service_minutes(periods, received_at, answered_at)
                    entries.append((priority, "answered", elapsed))
                elif ticket["status"] == "closed":
                    # Closed tickets whose follow-up stays unanswered count
                    # separately and never count as overdue.
                    entries.append((priority, "closed_without_answer", None))
                else:
                    if periods is None:
                        elapsed = as_of - received_at
                    else:
                        elapsed = _service_minutes(periods, received_at, as_of)
                    entries.append((priority, "pending", elapsed))
        groups = []
        for priority_name in PRIORITIES:
            target = target_minutes[priority_name]
            counts = {"total": 0, "answered": 0, "on_time": 0, "late": 0,
                      "pending": 0, "overdue": 0, "closed_without_answer": 0}
            for entry_priority, outcome, elapsed in entries:
                if entry_priority != priority_name:
                    continue
                counts["total"] += 1
                if outcome == "answered":
                    counts["answered"] += 1
                    # Meeting the target exactly counts as on time; only greater
                    # elapsed minutes are late.
                    if elapsed <= target:
                        counts["on_time"] += 1
                    else:
                        counts["late"] += 1
                elif outcome == "closed_without_answer":
                    counts["closed_without_answer"] += 1
                else:
                    counts["pending"] += 1
                    # Overdue wait must strictly exceed the target.
                    if elapsed > target:
                        counts["overdue"] += 1
            # on_time is a subset of answered; overdue is a subset of pending.
            rate = counts["on_time"] / counts["answered"] if counts["answered"] else None
            groups.append({"priority": priority_name, "target_minutes": target, **counts,
                           "on_time_rate": rate})
        return {"as_of": as_of, "groups": groups}

    def response_target_report(self, as_of, targets=None, since=None, until=None, service_periods=None):
        as_of = minute(as_of, "as_of")
        target_minutes = {"urgent": 5, "high": 15, "normal": 30, "low": 60}
        if targets is not None:
            if not isinstance(targets, dict):
                raise ValueError("targets must be an object or null")
            for priority, value in targets.items():
                if priority not in PRIORITY_RANK:
                    raise ValueError("targets has an unknown priority: " + str(priority))
                target_minutes[priority] = positive(value, "targets." + priority)
        if since is not None:
            since = minute(since, "since")
        if until is not None:
            until = minute(until, "until")
        if since is not None and until is not None and until < since:
            raise ValueError("until must not be earlier than since")
        periods = _service_periods(service_periods)
        tickets = list(self._read().get("tickets", {}).values())
        if since is None and until is None:
            # No window: every ticket takes part, untimed ones included.
            selected = tickets
        else:
            # The window selects by opened_at only: since is inclusive, until
            # exclusive and an omitted/null bound means no limit on that side;
            # equal bounds make the interval empty. Tickets without opened_at
            # never enter a windowed query, and response, close or reopen times
            # play no part in the selection.
            selected = [ticket for ticket in tickets
                        if "opened_at" in ticket
                        and (since is None or ticket["opened_at"] >= since)
                        and (until is None or ticket["opened_at"] < until)]
        for ticket in selected:
            if "opened_at" not in ticket:
                continue
            if ticket["opened_at"] > as_of:
                raise ValueError("opened_at must not be later than as_of")
            response = ticket.get("first_response")
            # Missing, null or an empty object carries no response time to check.
            if isinstance(response, dict) and response and response["responded_at"] > as_of:
                raise ValueError("responded_at must not be later than as_of")
        groups = []
        for priority in PRIORITIES:
            target = target_minutes[priority]
            counts = {"responded": 0, "on_time": 0, "late": 0, "pending": 0,
                      "overdue": 0, "untimed": 0, "closed_without_response": 0}
            for ticket in selected:
                if ticket.get("priority", "normal") != priority:
                    continue
                if "opened_at" not in ticket:
                    counts["untimed"] += 1
                    continue
                response = ticket.get("first_response")
                # Missing, null or an empty object means there is no response:
                # open tickets are pending, closed ones closed_without_response.
                if isinstance(response, dict) and response:
                    counts["responded"] += 1
                    if periods is None:
                        elapsed = response["responded_at"] - ticket["opened_at"]
                    else:
                        # Only minutes covered by a service period count; periods
                        # before registration or after the response contribute nothing.
                        elapsed = _service_minutes(periods, ticket["opened_at"],
                                                  response["responded_at"])
                    if elapsed <= target:
                        counts["on_time"] += 1
                    else:
                        counts["late"] += 1
                elif ticket["status"] == "closed":
                    counts["closed_without_response"] += 1
                else:
                    counts["pending"] += 1
                    if periods is None:
                        elapsed = as_of - ticket["opened_at"]
                    else:
                        elapsed = _service_minutes(periods, ticket["opened_at"], as_of)
                    if elapsed > target:
                        counts["overdue"] += 1
            rate = counts["on_time"] / counts["responded"] if counts["responded"] else None
            groups.append({"priority": priority, "target_minutes": target, **counts, "on_time_rate": rate})
        return {"as_of": as_of, "groups": groups}

    def response_target_queue(self, as_of, targets=None, service_periods=None):
        # Parameters are validated even when the store holds no data.
        as_of = minute(as_of, "as_of")
        target_minutes = {"urgent": 5, "high": 15, "normal": 30, "low": 60}
        if targets is not None:
            if not isinstance(targets, dict):
                raise ValueError("targets must be an object or null")
            for priority, value in targets.items():
                if priority not in PRIORITY_RANK:
                    raise ValueError("targets has an unknown priority: " + str(priority))
                target_minutes[priority] = positive(value, "targets." + priority)
        periods = _service_periods(service_periods)
        items = []
        untimed = 0
        for ticket in self._read().get("tickets", {}).values():
            # Only open tickets are candidates; responded and closed tickets are
            # excluded, and their future times are never checked. Assignment is
            # irrelevant to membership.
            if ticket["status"] != "open":
                continue
            response = ticket.get("first_response")
            # Missing, null or an empty object means the ticket still awaits
            # its first response; only a non-empty object removes it.
            if isinstance(response, dict) and response:
                continue
            if "opened_at" not in ticket:
                # No registration time: counted as untimed and never queued.
                untimed += 1
                continue
            if ticket["opened_at"] > as_of:
                raise ValueError("opened_at must not be later than as_of")
            target = target_minutes[ticket.get("priority", "normal")]
            if periods is None:
                waiting = as_of - ticket["opened_at"]
                due_at = ticket["opened_at"] + target
            else:
                # Only minutes covered by a service period count; merged periods
                # make overlapping, repeated and touching intervals count once.
                waiting = _service_minutes(periods, ticket["opened_at"], as_of)
                # The due simulation may run beyond as_of using the periods given;
                # coverage that can never reach the target leaves due_at null.
                due_at = _due_minute(periods, ticket["opened_at"], target)
            items.append({"ticket": ticket, "waiting_minutes": waiting,
                          "due_at": due_at, "overdue": waiting > target})
        # Overdue tickets first, then urgent/high/normal/low (missing priority is
        # normal), then ascending due time with nulls last, finally the
        # case-sensitive ticket id.
        items.sort(key=lambda item: (not item["overdue"],
                                     PRIORITY_RANK[item["ticket"].get("priority", "normal")],
                                     item["due_at"] is None,
                                     item["due_at"] if item["due_at"] is not None else 0,
                                     item["ticket"]["ticket_id"]))
        return {"untimed": untimed, "items": items}

    def customer_response_target_queue(self, as_of, targets=None, service_periods=None):
        # Parameters are validated even when the store holds no data.
        as_of = minute(as_of, "as_of")
        target_minutes = {"urgent": 5, "high": 15, "normal": 30, "low": 60}
        if targets is not None:
            if not isinstance(targets, dict):
                raise ValueError("targets must be an object or null")
            for priority, value in targets.items():
                if priority not in PRIORITY_RANK:
                    raise ValueError("targets has an unknown priority: " + str(priority))
                target_minutes[priority] = positive(value, "targets." + priority)
        periods = _service_periods(service_periods)
        items = []
        for ticket in self._read().get("tickets", {}).values():
            # Only open tickets with a follow-up history participate; closed
            # tickets and tickets without follow-ups leave the queue and skip
            # every time check. Assignment is irrelevant to membership.
            if ticket["status"] != "open":
                continue
            messages = ticket.get("customer_messages")
            if not messages:
                continue
            response = ticket.get("first_response")
            # Missing, null or an empty object means there is no first response;
            # handwritten and knowledge answers both supply reply times.
            response_at = response["responded_at"] if isinstance(response, dict) and response else None
            answer_times = ([response_at] if response_at is not None else [])
            answer_times.extend(reply["replied_at"] for reply in ticket.get("replies") or [])
            # Any follow-up or answer later than as_of invalidates the whole
            # query even when every follow-up is already covered; empty service
            # periods do not waive this check.
            if any(message["received_at"] > as_of for message in messages) or \
                    any(time > as_of for time in answer_times):
                raise ValueError("customer message or response time must not be later than as_of")
            # The answer boundary is the latest of the first response and all
            # later replies; a follow-up in the same minute is already covered
            # and only strictly later follow-ups remain unanswered. With no
            # answer every follow-up stays unanswered; duplicate records count.
            boundary = max(answer_times) if answer_times else None
            unanswered = [message for message in messages
                          if boundary is None or message["received_at"] > boundary]
            if not unanswered:
                continue
            earliest = min(message["received_at"] for message in unanswered)
            target = target_minutes[ticket.get("priority", "normal")]
            if periods is None:
                waiting = as_of - earliest
                due_at = earliest + target
            else:
                # Only minutes covered by a service period count; merged periods
                # make overlapping, repeated and touching intervals count once,
                # and an empty period list leaves the wait at zero.
                waiting = _service_minutes(periods, earliest, as_of)
                # The due simulation may run beyond as_of using the periods
                # given and may land on a period end; coverage that can never
                # reach the target leaves due_at null.
                due_at = _due_minute(periods, earliest, target)
            items.append({"ticket": ticket, "count": len(unanswered),
                          "waiting_minutes": waiting, "due_at": due_at,
                          "overdue": waiting > target})
        # Overdue tickets first, then urgent/high/normal/low (missing priority is
        # normal), then ascending due time with nulls last, finally the
        # case-sensitive ticket id.
        items.sort(key=lambda item: (not item["overdue"],
                                     PRIORITY_RANK[item["ticket"].get("priority", "normal")],
                                     item["due_at"] is None,
                                     item["due_at"] if item["due_at"] is not None else 0,
                                     item["ticket"]["ticket_id"]))
        return {"as_of": as_of, "items": items}

    def assignee_workload_report(self, as_of, response_minutes=30, follow_up_minutes=60):
        as_of = minute(as_of, "as_of")
        response_minutes = positive(response_minutes, "response_minutes")
        follow_up_minutes = positive(follow_up_minutes, "follow_up_minutes")
        groups = {}
        def bucket(assignee):
            return groups.setdefault(assignee, {"assignee": assignee, "open": 0, "pending": 0,
                                               "overdue": 0, "follow_up": 0,
                                               "follow_up_overdue": 0, "untimed": 0})
        for ticket in self._read().get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            counts = bucket(ticket.get("assignee"))
            counts["open"] += 1
            response = ticket.get("first_response")
            # Only a non-empty first-response object counts as responded; missing, null or {} are pending.
            if not isinstance(response, dict) or not response:
                if "opened_at" in ticket:
                    if ticket["opened_at"] > as_of:
                        raise ValueError("opened_at must not be later than as_of")
                    counts["pending"] += 1
                    if as_of - ticket["opened_at"] > response_minutes:
                        counts["overdue"] += 1
                else:
                    counts["untimed"] += 1
            else:
                counts["follow_up"] += 1
                replies = ticket.get("replies")
                if replies:
                    last_answered_at = replies[-1]["replied_at"]
                else:
                    last_answered_at = response["responded_at"]
                if response["responded_at"] > as_of or any(reply["replied_at"] > as_of for reply in replies or []):
                    raise ValueError("response time must not be later than as_of")
                if as_of - last_answered_at > follow_up_minutes:
                    counts["follow_up_overdue"] += 1
        ordered = [groups[name] for name in sorted(n for n in groups if n is not None)]
        if None in groups:
            ordered.insert(0, groups[None])
        return {"as_of": as_of, "groups": ordered}

    def publish_knowledge(self, article_id, ticket_id):
        article_id, ticket_id = text(article_id, "article_id"), text(ticket_id, "ticket_id")
        data = self._read()
        knowledge = data.get("knowledge", {})
        if article_id in knowledge:
            raise ValueError("article already exists")
        if any(entry["source_ticket_id"] == ticket_id for entry in knowledge.values()):
            raise ValueError("ticket already published")
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "closed":
            raise ValueError("source ticket must exist and be closed")
        entry = {"article_id": article_id, "source_ticket_id": ticket_id, "title": ticket["subject"], "content": ticket["resolution"]}
        data.setdefault("knowledge", {})[article_id] = entry
        data.setdefault("knowledge_history", {})[article_id] = [
            {"revision": 1, "title": entry["title"], "content": entry["content"]}
        ]
        self._write(data)
        return entry

    def _append_revision(self, data, article_id, entry, title, content):
        histories = data.setdefault("knowledge_history", {})
        history = histories.get(article_id)
        if history is None:
            # Articles written before history existed keep their current content as revision 1.
            history = [{"revision": 1, "title": entry["title"], "content": entry["content"]}]
            histories[article_id] = history
        history.append({"revision": history[-1]["revision"] + 1, "title": title, "content": content})
        entry["title"] = title
        entry["content"] = content

    def update_knowledge(self, article_id, title, content):
        article_id, title, content = (text(article_id, "article_id"),
                                      text(title, "title"),
                                      text(content, "content"))
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        if title != entry["title"] or content != entry["content"]:
            self._append_revision(data, article_id, entry, title, content)
            self._write(data)
        return entry

    def knowledge_history(self, article_id):
        article_id = text(article_id, "article_id")
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        history = data.get("knowledge_history", {}).get(article_id)
        if history is None:
            # Old articles without stored history use their current content as revision 1;
            # the missing record is never reconstructed or backfilled.
            history = [{"revision": 1, "title": entry["title"], "content": entry["content"]}]
        return [{"revision": item["revision"], "title": item["title"], "content": item["content"]}
                for item in history]

    def restore_knowledge(self, article_id, revision):
        article_id = text(article_id, "article_id")
        revision = positive(revision, "revision")
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        history = data.get("knowledge_history", {}).get(article_id)
        if history is None:
            source = {"revision": 1, "title": entry["title"], "content": entry["content"]} if revision == 1 else None
        else:
            source = next((item for item in history if item["revision"] == revision), None)
        if source is None:
            raise ValueError("unknown knowledge revision")
        if source["title"] != entry["title"] or source["content"] != entry["content"]:
            self._append_revision(data, article_id, entry, source["title"], source["content"])
            self._write(data)
        return entry

    def search_knowledge(self, query=None):
        if query is None:
            terms = None
        elif not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        else:
            terms = [term.casefold() for term in query.strip().split()]
        data = self._read()
        states = data.get("knowledge_enabled", {})
        entries = sorted((entry for entry in data.get("knowledge", {}).values()
                          if states.get(entry["article_id"], True)),
                         key=lambda entry: entry["article_id"])
        if terms is None:
            return entries
        return [entry for entry in entries
                if all(term in entry["title"].casefold() or term in entry["content"].casefold() for term in terms)]

    def set_knowledge_enabled(self, article_id, enabled):
        article_id = text(article_id, "article_id")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        data.setdefault("knowledge_enabled", {})[article_id] = enabled
        self._write(data)
        return {"article": entry, "enabled": enabled}

    def knowledge_usage_report(self, since=0, until=None):
        since = minute(since, "since")
        if until is not None:
            until = minute(until, "until")
            if until < since:
                raise ValueError("until must not be earlier than since")
        data = self._read()
        usage = {}
        def record(article_id, ticket_id, at, kind):
            if at < since or (until is not None and at >= until):
                return
            stats = usage.setdefault(article_id, {"first_responses": 0, "replies": 0, "tickets": set(), "last_used_at": None})
            stats[kind] += 1
            stats["tickets"].add(ticket_id)
            if stats["last_used_at"] is None or at > stats["last_used_at"]:
                stats["last_used_at"] = at
        for ticket in data.get("tickets", {}).values():
            response = ticket.get("first_response")
            if response is not None and response.get("knowledge") is not None:
                record(response["knowledge"]["article_id"], ticket["ticket_id"], response["responded_at"], "first_responses")
            for reply in ticket.get("replies") or []:
                if reply.get("knowledge") is not None:
                    record(reply["knowledge"]["article_id"], ticket["ticket_id"], reply["replied_at"], "replies")
        states = data.get("knowledge_enabled", {})
        report = []
        for article_id in sorted(data.get("knowledge", {})):
            stats = usage.get(article_id)
            report.append({
                "article": data["knowledge"][article_id],
                "enabled": states.get(article_id, True),
                "first_responses": stats["first_responses"] if stats else 0,
                "replies": stats["replies"] if stats else 0,
                "ticket_count": len(stats["tickets"]) if stats else 0,
                "last_used_at": stats["last_used_at"] if stats else None,
            })
        return report

    def _stale_references(self, ticket, entry, article_id):
        def stale(snapshot):
            # Only the saved title/content are compared; revision, source and enabled state are not.
            return snapshot["title"] != entry["title"] or snapshot["content"] != entry["content"]
        references = []
        response = ticket.get("first_response")
        # Missing, null or empty first responses carry no reference.
        if isinstance(response, dict):
            knowledge = response.get("knowledge")
            if knowledge is not None and knowledge["article_id"] == article_id and stale(knowledge):
                references.append({"kind": "first_response", "index": None})
        for index, reply in enumerate(ticket.get("replies") or []):
            knowledge = reply.get("knowledge")
            if knowledge is not None and knowledge["article_id"] == article_id and stale(knowledge):
                references.append({"kind": "reply", "index": index})
        return references

    def _review_key(self, reference):
        if reference["kind"] == "first_response":
            return "first_response"
        return "reply:" + str(reference["index"])

    def review_knowledge(self, ticket_id, article_id):
        ticket_id, article_id = text(ticket_id, "ticket_id"), text(article_id, "article_id")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] != "open":
            raise ValueError("ticket must exist and be open")
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        references = self._stale_references(ticket, entry, article_id)
        if references:
            # A confirmation stamps the article's current title/content per covered
            # reference; it stays valid only while the current content still matches.
            confirmations = (data.setdefault("knowledge_reviews", {})
                             .setdefault(ticket_id, {}).setdefault(article_id, {}))
            for reference in references:
                confirmations[self._review_key(reference)] = {"title": entry["title"],
                                                              "content": entry["content"]}
            self._write(data)
        return {"ticket_id": ticket_id, "article_id": article_id,
                "reviewed_count": len(references)}

    def knowledge_review_queue(self, article_id, offset=0, limit=20, pending_only=False):
        article_id = text(article_id, "article_id")
        # bool is a subclass of int, so compare types explicitly; floats and strings are rejected too.
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        if type(pending_only) is not bool:
            raise ValueError("pending_only must be a boolean")
        data = self._read()
        entry = data.get("knowledge", {}).get(article_id)
        if entry is None:
            raise ValueError("unknown knowledge article")
        reviews = data.get("knowledge_reviews", {})
        current = {"title": entry["title"], "content": entry["content"]}
        matched = []
        for ticket in data.get("tickets", {}).values():
            if ticket["status"] != "open":
                continue
            references = self._stale_references(ticket, entry, article_id)
            if pending_only:
                confirmations = reviews.get(ticket["ticket_id"], {}).get(article_id, {})
                references = [reference for reference in references
                              if confirmations.get(self._review_key(reference)) != current]
            if references:
                matched.append({"ticket": ticket, "references": references})
        matched.sort(key=lambda item: item["ticket"]["ticket_id"])
        return {"total": len(matched), "items": matched[offset:offset + limit]}

    def correct_knowledge(self, article_id, ticket_ids, replied_at):
        article_id = text(article_id, "article_id")
        if not isinstance(ticket_ids, list) or not ticket_ids:
            raise ValueError("ticket_ids must be a nonempty array")
        ids = []
        for element in ticket_ids:
            if not isinstance(element, str) or not element.strip():
                raise ValueError("ticket_ids elements must be nonblank strings")
            ids.append(element.strip())
        if len(set(ids)) != len(ids):
            raise ValueError("ticket_ids must not contain duplicate ids")
        replied_at = minute(replied_at, "replied_at")
        data = self._read()
        snapshot, _ = self._knowledge_snapshot(data, article_id, None)
        entry = data["knowledge"][article_id]
        tickets = data.get("tickets", {})
        reviews = data.get("knowledge_reviews", {})
        current = {"title": entry["title"], "content": entry["content"]}
        targets = []
        for ticket_id in ids:
            ticket = tickets.get(ticket_id)
            if ticket is None or ticket["status"] != "open":
                raise ValueError("ticket must exist and be open")
            if "opened_at" not in ticket:
                raise ValueError("ticket has no opened_at")
            response = ticket.get("first_response")
            # Missing, null or an empty object means there is no first response.
            if not isinstance(response, dict) or not response:
                raise ValueError("ticket has no first response")
            references = self._stale_references(ticket, entry, article_id)
            confirmations = reviews.get(ticket_id, {}).get(article_id, {})
            pending = [reference for reference in references
                       if confirmations.get(self._review_key(reference)) != current]
            if not pending:
                raise ValueError("ticket has no unconfirmed stale reference to the article")
            self._check_reply_time(ticket, replied_at)
            targets.append((ticket, ticket_id, references))
        corrected = []
        for ticket, ticket_id, references in targets:
            ticket.setdefault("replies", []).append({
                "message": snapshot["content"],
                "replied_at": replied_at,
                "knowledge": dict(snapshot),
            })
            # Confirm every stale reference present at submission against the
            # current title/content, exactly like review_knowledge does.
            confirmations = (data.setdefault("knowledge_reviews", {})
                             .setdefault(ticket_id, {}).setdefault(article_id, {}))
            for reference in references:
                confirmations[self._review_key(reference)] = {"title": entry["title"],
                                                              "content": entry["content"]}
            corrected.append(ticket)
        self._write(data)
        return corrected

    def auto_assign(self, assignees, max_open=5):
        if not isinstance(assignees, list) or not assignees:
            raise ValueError("assignees must be a nonempty array")
        names = []
        for element in assignees:
            if not isinstance(element, str) or not element.strip():
                raise ValueError("assignees elements must be nonblank strings")
            names.append(element.strip())
        if len(set(names)) != len(names):
            raise ValueError("assignees must not contain duplicate names")
        max_open = positive(max_open, "max_open")
        data = self._read()
        tickets = data.get("tickets", {})
        loads = {name: 0 for name in names}
        for ticket in tickets.values():
            if ticket["status"] == "open" and ticket.get("assignee") in loads:
                loads[ticket["assignee"]] += 1
        def candidate_key(ticket):
            opened_at = ticket.get("opened_at")
            return (PRIORITY_RANK[ticket.get("priority", "normal")],
                    opened_at is None, opened_at if opened_at is not None else 0,
                    ticket["ticket_id"])
        candidates = sorted((ticket for ticket in tickets.values()
                             if ticket["status"] == "open" and ticket.get("assignee") is None),
                            key=candidate_key)
        assigned = []
        assigned_ids = set()
        for ticket in candidates:
            available = [name for name in names if loads[name] < max_open]
            if not available:
                break
            chosen = min(available, key=lambda name: (loads[name], name))
            ticket["assignee"] = chosen
            loads[chosen] += 1
            assigned.append(ticket)
            assigned_ids.add(ticket["ticket_id"])
        if assigned:
            self._write(data)
        remaining = [ticket["ticket_id"] for ticket in candidates
                     if ticket["ticket_id"] not in assigned_ids]
        return {"assigned": assigned, "remaining": remaining}

    def route_assign(self, rules, max_open=5, strategy="greedy", assignee_limits=None):
        if strategy not in ("greedy", "coverage"):
            raise ValueError("strategy must be greedy or coverage")
        if not isinstance(rules, list) or not rules:
            raise ValueError("rules must be a nonempty array")
        normalized = []
        categories = set()
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {"category", "assignees"}:
                raise ValueError("each rule must be an object with only category and assignees")
            category = rule["category"]
            if category is not None:
                if not isinstance(category, str) or not category.strip():
                    raise ValueError("rule category must be null or a nonblank string")
                category = category.strip()
            if category in categories:
                raise ValueError("rules must not contain duplicate categories")
            categories.add(category)
            raw_assignees = rule["assignees"]
            if not isinstance(raw_assignees, list) or not raw_assignees:
                raise ValueError("assignees must be a nonempty array")
            names = []
            for element in raw_assignees:
                if not isinstance(element, str) or not element.strip():
                    raise ValueError("assignees elements must be nonblank strings")
                names.append(element.strip())
            if len(set(names)) != len(names):
                raise ValueError("assignees must not contain duplicate names")
            normalized.append((category, names))
        max_open = positive(max_open, "max_open")
        known_names = {name for _, names in normalized for name in names}
        # Per-person ceilings for this call only; omitted, null or an empty
        # object means everyone shares max_open. Names strip outer whitespace,
        # stay case-sensitive and must appear in this call's rules.
        limits = {}
        if assignee_limits is not None:
            if not isinstance(assignee_limits, dict):
                raise ValueError("assignee_limits must be an object or null")
            for raw_name, value in assignee_limits.items():
                if not isinstance(raw_name, str) or not raw_name.strip():
                    raise ValueError("assignee_limits names must be nonblank strings")
                name = raw_name.strip()
                if name in limits:
                    raise ValueError("assignee_limits must not contain duplicate names")
                if name not in known_names:
                    raise ValueError("assignee_limits has an unknown assignee: " + name)
                # bool is a subclass of int, so compare types explicitly; floats,
                # strings, null and negative values are rejected too.
                if type(value) is not int or value < 0:
                    raise ValueError("assignee_limits." + name + " must be a nonnegative integer")
                limits[name] = value
        data = self._read()
        tickets = data.get("tickets", {})
        rule_by_category = {category: names for category, names in normalized}
        loads = {}
        for _, names in normalized:
            for name in names:
                loads.setdefault(name, 0)
        # Load is the current count of open tickets across every category; a name
        # shared by several rules carries one shared load.
        for ticket in tickets.values():
            if ticket["status"] == "open" and ticket.get("assignee") in loads:
                loads[ticket["assignee"]] += 1
        def candidate_key(ticket):
            opened_at = ticket.get("opened_at")
            return (PRIORITY_RANK[ticket.get("priority", "normal")],
                    opened_at is None, opened_at if opened_at is not None else 0,
                    ticket["ticket_id"])
        candidates = sorted((ticket for ticket in tickets.values()
                             if ticket["status"] == "open" and ticket.get("assignee") is None),
                            key=candidate_key)
        assigned = []
        assigned_ids = set()
        if strategy == "greedy":
            for ticket in candidates:
                # Each ticket uses only the rule for its exact category; the
                # uncategorized rule never backstops a categorized ticket.
                names = rule_by_category.get(ticket.get("category"))
                if names is None:
                    continue
                available = [name for name in names if loads[name] < limits.get(name, max_open)]
                if not available:
                    continue
                chosen = min(available, key=lambda name: (loads[name], name))
                ticket["assignee"] = chosen
                loads[chosen] += 1
                assigned.append(ticket)
                assigned_ids.add(ticket["ticket_id"])
        else:
            # Pick the global plan with the most assignments; ties prefer
            # assigning the earlier candidate, then the lexicographically
            # smallest recipient sequence in candidate order.
            ceilings = {name: limits.get(name, max_open) for name in loads}
            choices = _route_coverage(candidates, rule_by_category, loads, ceilings)
            for ticket, chosen in zip(candidates, choices):
                if chosen is None:
                    continue
                ticket["assignee"] = chosen
                assigned.append(ticket)
                assigned_ids.add(ticket["ticket_id"])
        if assigned:
            self._write(data)
        remaining = [ticket["ticket_id"] for ticket in candidates
                     if ticket["ticket_id"] not in assigned_ids]
        return {"assigned": assigned, "remaining": remaining}

    def route_handover(self, source_assignee, rules, reason, transferred_at, max_open=5):
        source_assignee = text(source_assignee, "source_assignee")
        reason = text(reason, "reason")
        if not isinstance(rules, list) or not rules:
            raise ValueError("rules must be a nonempty array")
        normalized = []
        categories = set()
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {"category", "assignees"}:
                raise ValueError("each rule must be an object with only category and assignees")
            category = rule["category"]
            if category is not None:
                if not isinstance(category, str) or not category.strip():
                    raise ValueError("rule category must be null or a nonblank string")
                category = category.strip()
            if category in categories:
                raise ValueError("rules must not contain duplicate categories")
            categories.add(category)
            raw_assignees = rule["assignees"]
            if not isinstance(raw_assignees, list) or not raw_assignees:
                raise ValueError("assignees must be a nonempty array")
            names = []
            for element in raw_assignees:
                if not isinstance(element, str) or not element.strip():
                    raise ValueError("assignees elements must be nonblank strings")
                names.append(element.strip())
            if len(set(names)) != len(names):
                raise ValueError("assignees must not contain duplicate names")
            if source_assignee in names:
                raise ValueError("assignees must not contain the source assignee")
            normalized.append((category, names))
        transferred_at = minute(transferred_at, "transferred_at")
        max_open = positive(max_open, "max_open")
        data = self._read()
        tickets = data.get("tickets", {})
        def candidate_key(ticket):
            opened_at = ticket.get("opened_at")
            return (PRIORITY_RANK[ticket.get("priority", "normal")],
                    opened_at is None, opened_at if opened_at is not None else 0,
                    ticket["ticket_id"])
        selected = sorted((ticket for ticket in tickets.values()
                           if ticket["status"] == "open" and ticket.get("assignee") == source_assignee),
                          key=candidate_key)
        if not selected:
            return []
        rule_by_category = {category: names for category, names in normalized}
        loads = {}
        for _, names in normalized:
            for name in names:
                loads.setdefault(name, 0)
        # Load is the current count of open tickets across every category; a name
        # shared by several rules carries one shared load and one shared capacity.
        for ticket in tickets.values():
            if ticket["status"] == "open" and ticket.get("assignee") in loads:
                loads[ticket["assignee"]] += 1
        ceilings = {name: max_open for name in loads}
        # Every selected ticket must be placed by its own category's rule (the
        # uncategorized rule never backstops a categorized ticket); among complete
        # plans the lexicographically smallest recipient sequence in candidate
        # order wins, so rule and name input order cannot change the result.
        choices = _route_coverage(selected, rule_by_category, loads, ceilings)
        if any(chosen is None for chosen in choices):
            raise ValueError("rules do not cover all selected tickets within capacity")
        for ticket in selected:
            if "opened_at" in ticket and transferred_at < ticket["opened_at"]:
                raise ValueError("transferred_at must not be earlier than opened_at")
            history = ticket.get("transfer_history")
            if history and transferred_at < history[-1]["transferred_at"]:
                raise ValueError("transferred_at must not be earlier than the last transfer")
        handed_over = []
        for ticket, chosen in zip(selected, choices):
            ticket.setdefault("transfer_history", []).append(
                {"from_assignee": ticket["assignee"], "to_assignee": chosen,
                 "reason": reason, "transferred_at": transferred_at})
            ticket["assignee"] = chosen
            handed_over.append(ticket)
        self._write(data)
        return handed_over

    def auto_categorize(self, rules):
        if not isinstance(rules, list) or not rules:
            raise ValueError("rules must be a nonempty array")
        normalized = []
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {"category", "query"}:
                raise ValueError("each rule must be an object with only category and query")
            category = rule["category"]
            if not isinstance(category, str) or not category.strip():
                raise ValueError("rule category must be a nonblank string")
            query = rule["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("rule query must be a nonblank string")
            # Queries split on whitespace and fold case; punctuation stays part of
            # the token and matches literally. Several rules may share a category.
            normalized.append((category.strip(),
                               [term.casefold() for term in query.strip().split()]))
        data = self._read()
        candidates = [ticket for ticket in data.get("tickets", {}).values()
                      if ticket["status"] == "open" and ticket.get("category") is None]
        classified = []
        remaining = []
        for ticket in candidates:
            # Only the subject and customer follow-up bodies are searched; customer
            # name, notes, resolution, agent replies and knowledge stay out. A ticket
            # without a follow-up history simply matches the subject alone.
            haystacks = [ticket["subject"].casefold()]
            haystacks.extend(message["message"].casefold()
                             for message in ticket.get("customer_messages") or [])
            chosen = None
            # Rule order decides priority; the first matching rule wins.
            for category, terms in normalized:
                # Each term must be a contiguous substring of one record; different
                # terms may hit different records, but a term never spans records.
                if all(any(term in haystack for haystack in haystacks) for term in terms):
                    chosen = category
                    break
            if chosen is None:
                remaining.append(ticket["ticket_id"])
            else:
                ticket["category"] = chosen
                classified.append(ticket)
        classified.sort(key=lambda ticket: ticket["ticket_id"])
        remaining.sort()
        if classified:
            self._write(data)
        return {"classified": classified, "remaining": remaining}

    def conversation(self, ticket_id, offset=0, limit=20):
        ticket_id = text(ticket_id, "ticket_id")
        # bool is a subclass of int, so compare types explicitly; floats and strings are rejected too.
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        ticket = self._read().get("tickets", {}).get(ticket_id)
        if ticket is None:
            raise ValueError("unknown ticket")
        entries = []
        for index, message in enumerate(ticket.get("customer_messages") or []):
            entries.append({"kind": "customer_message", "index": index,
                            "at": message["received_at"], "record": message})
        response = ticket.get("first_response")
        # Missing, null or an empty object means there is no first response.
        if isinstance(response, dict) and response:
            entries.append({"kind": "first_response", "index": None,
                            "at": response["responded_at"], "record": response})
        for index, reply in enumerate(ticket.get("replies") or []):
            entries.append({"kind": "reply", "index": index,
                            "at": reply["replied_at"], "record": reply})
        order = {"customer_message": 0, "first_response": 1, "reply": 2}
        entries.sort(key=lambda entry: (entry["at"], order[entry["kind"]],
                                        entry["index"] if entry["index"] is not None else 0))
        return {"ticket_id": ticket_id, "total": len(entries),
                "items": entries[offset:offset + limit]}

    def list_tickets(self, status=None):
        if status not in (None, "open", "closed"):
            raise ValueError("status must be open or closed")
        return sorted((t for t in self._read().get("tickets", {}).values() if status is None or t["status"] == status), key=lambda t: t["ticket_id"])

    def set_category(self, ticket_id, category):
        ticket_id = text(ticket_id, "ticket_id")
        if category is not None:
            category = text(category, "category")
        data = self._read()
        ticket = data.get("tickets", {}).get(ticket_id)
        if ticket is None or ticket["status"] == "closed":
            raise ValueError("ticket must exist and be open")
        if category is None:
            ticket.pop("category", None)
        else:
            ticket["category"] = category
        self._write(data)
        return ticket

    def list_by_category(self, category, status=None):
        if category is not None:
            category = text(category, "category")
        if status not in (None, "open", "closed"):
            raise ValueError("status must be open or closed")
        tickets = self._read().get("tickets", {}).values()
        if category is None:
            matches = (t for t in tickets if t.get("category") is None)
        else:
            matches = (t for t in tickets if t.get("category") == category)
        return sorted((t for t in matches if status is None or t["status"] == status), key=lambda t: t["ticket_id"])

    def search_tickets(self, query, status=None, category=_UNSET, offset=0, limit=20):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        if status not in (None, "open", "closed"):
            raise ValueError("status must be open or closed")
        if category is not _UNSET and category is not None:
            category = text(category, "category")
        # bool is a subclass of int, so compare types explicitly; floats and strings are rejected too.
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        terms = [term.casefold() for term in query.strip().split()]
        matched = []
        for ticket in self._read().get("tickets", {}).values():
            if status is not None and ticket["status"] != status:
                continue
            if category is _UNSET:
                pass
            elif category is None:
                if ticket.get("category") is not None:
                    continue
            elif ticket.get("category") != category:
                continue
            fields = [ticket["customer"], ticket["subject"]]
            fields.extend(ticket.get("notes") or [])
            if ticket.get("resolution") is not None:
                fields.append(ticket["resolution"])
            response = ticket.get("first_response")
            if response is not None and response.get("message"):
                # Knowledge replies are searched by the saved message body, not the snapshot.
                fields.append(response["message"])
            for reply in ticket.get("replies") or []:
                if reply.get("message"):
                    fields.append(reply["message"])
            haystacks = [field.casefold() for field in fields]
            if all(any(term in haystack for haystack in haystacks) for term in terms):
                matched.append(ticket)
        matched.sort(key=lambda ticket: ticket["ticket_id"])
        return {"total": len(matched), "items": matched[offset:offset + limit]}
