"""Algorithm-independent tokenization/statistics/interface for discovered lexical scorers."""

import ast

# This scaffold contains no algorithm scoring formula. Models supply the scorer;
# the final standalone module, including the scaffold, is tested in Docker.
SCAFFOLD = '''import re
from collections import Counter

def factory_tokens(text):
    terms = re.findall(r"[a-z0-9_]+", text.lower())
    for fragment in re.findall(r"[\\u4e00-\\u9fff]+", text):
        terms.extend(fragment)
        terms.extend(fragment[i:i+2] for i in range(len(fragment)-1))
    return terms

def retrieve(query, memories, top_k=5):
    if not query.strip() or not memories or top_k <= 0:
        return []
    query_terms = Counter(factory_tokens(query))
    if not query_terms:
        return []
    documents = [Counter(factory_tokens(memory['text'])) for memory in memories]
    df = Counter()
    for document in documents:
        df.update(document.keys())
    stats = {'N': len(memories), 'avg_len': sum(sum(doc.values()) for doc in documents) / len(memories),
             'df': dict(df)}
    hits = []
    for memory, document in zip(memories, documents):
        score = float(score_document(query_terms, document, stats))
        if score != 0:
            hits.append({'id': memory['id'], 'score': score})
    return sorted(hits, key=lambda hit: (-hit['score'], hit['id']))[:top_k]
'''


TERM_SCAFFOLD = SCAFFOLD.replace(
    "score = float(score_document(query_terms, document, stats))",
    "score = sum(float(score_term(document[term], df[term], sum(document.values()), stats['avg_len'], stats['N'])) "
    "* query_terms[term] for term in sorted(query_terms) if df[term] > 0)")


def compose_retrieval(scorer, mode='document', kernels=''):
    """Protect scaffold names and require an explicit, statically inspectable hook."""
    try:
        tree = ast.parse(scorer)
    except SyntaxError as error:
        raise ValueError('Scoring function has invalid Python syntax') from error
    hook = 'score_term' if mode == 'term' else 'score_document'
    arguments = ['tf', 'df', 'doc_len', 'avg_len', 'N'] if mode == 'term' else ['query_terms', 'document_terms', 'corpus_stats']
    hooks = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == hook]
    if len(hooks) != 1:
        raise ValueError('Expected one ' + hook + '(' + ', '.join(arguments) + ') function')
    args = hooks[0].args
    if ([arg.arg for arg in args.args] != arguments
            or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults):
        raise ValueError('Scoring function signature must match the declared hook')
    for node in ast.walk(tree):
        name = node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else node.id if isinstance(node, ast.Name) else None
        if name and (name == 'retrieve' or name.startswith('factory_')):
            raise ValueError('Generated scorer cannot redefine or reference reserved scaffold names')
        if (name in {'source_tf_component', 'source_idf_component'}
                and not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load))):
            raise ValueError('Generated scorer cannot override adapted source kernels')
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((alias.asname or alias.name) in {'retrieve', 'factory_tokens', 'source_tf_component', 'source_idf_component'}
                   for alias in node.names):
                raise ValueError('Generated scorer cannot shadow scaffold imports')
    return (TERM_SCAFFOLD if mode == 'term' else SCAFFOLD) + '\n\n' + kernels + '\n\n' + scorer
