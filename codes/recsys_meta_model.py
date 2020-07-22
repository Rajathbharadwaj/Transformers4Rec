"""
A meta class supports various (Huggingface) transformer models for RecSys tasks.

"""

import logging
from collections import OrderedDict

import torch
from torch import nn
from torch.nn import functional as F

from transformers.modeling_utils import PreTrainedModel

logger = logging.getLogger(__name__)


class RecSysMetaModel(PreTrainedModel):
    """
    vocab_sizes : sizes of vocab for each discrete inputs
        e.g., [product_id_vocabs, category_vocabs, etc.]
    """
    def __init__(self, model, config, model_args, data_args):
        super(RecSysMetaModel, self).__init__(config)
        
        self.model = model 

        if self.model.__class__ in [nn.GRU, nn.LSTM, nn.RNN]:
            self.is_rnn = True
        else:
            self.is_rnn = False

        self.pad_token = data_args.pad_token

        # set embedding tables
        self.embedding_product = nn.Embedding(data_args.num_product, model_args.d_model, padding_idx=self.pad_token)
        self.embedding_category = nn.Embedding(data_args.num_category, model_args.d_model, padding_idx=self.pad_token)

        self.merge = model_args.merge_inputs
        
        if self.merge == 'concat_mlp':
            n_embeddings = data_args.num_categorical_features
            self.mlp_merge = nn.Linear(model_args.d_model * n_embeddings, model_args.d_model)
        
        self.similarity_type = model_args.similarity_type
        self.margin_loss = model_args.margin_loss
        self.output_layer = nn.Linear(model_args.d_model, data_args.num_product)
        self.loss_type = model_args.loss_type
        self.log_softmax = nn.LogSoftmax(dim=-1)
        
        if self.loss_type == 'cross_entropy':
            self.loss_fn = nn.NLLLoss(ignore_index=self.pad_token)
        elif self.loss_type == 'cross_entropy_neg':
            self.loss_fn = nn.NLLLoss()
        elif self.loss_type == 'cross_entropy_neg_1d':
            self.loss_fn = nll_1d
        elif self.loss_type == 'margin_hinge':
            # https://pytorch.org/docs/master/generated/torch.nn.CosineEmbeddingLoss.html
            self.loss_fn = nn.CosineEmbeddingLoss(margin=model_args.margin_loss, reduction='sum')
        else:
            raise NotImplementedError

        if self.similarity_type == 'concat_mlp':
            self.sim_mlp = nn.Sequential(
                OrderedDict([
                    ('linear0', nn.Linear(model_args.d_model * 2, model_args.d_model)),
                    ('relu0', nn.LeakyReLU()),
                    ('linear1', nn.Linear(model_args.d_model, model_args.d_model // 2)),
                    ('relu1', nn.LeakyReLU()),
                    ('linear2', nn.Linear(model_args.d_model // 2, model_args.d_model // 4)),
                    ('relu2', nn.LeakyReLU()),
                    ('linear3', nn.Linear(model_args.d_model // 4, 1)),
                ]       
            ))
        
    def _unflatten_neg_seq(self, neg_seq, seqlen):
        """
        neg_seq: n_batch x (num_neg_samples x max_seq_len); flattened. 2D.
        """
        assert neg_seq.dim() == 2

        n_batch, flatten_len = neg_seq.size()
        
        assert flatten_len % seqlen == 0

        n_neg_seqs_per_pos_seq = flatten_len // seqlen
        return neg_seq.reshape((n_batch, n_neg_seqs_per_pos_seq, seqlen))

    def forward(
        self,
        product_seq,
        category_seq,
        neg_product_seq,
        neg_category_seq,
    ):
        """
        For cross entropy loss, we split input and target BEFORE embedding layer.
        For margin hinge loss, we split input and target AFTER embedding layer.
        """
        
        # Step1. Obtain Embedding
        max_seq_len = product_seq.size(1)
        product_seq_trg = product_seq[:, 1:] 

        pos_prd_emb = self.embedding_product(product_seq)
        pos_cat_emb = self.embedding_category(category_seq)
        
        # unflatten negative sample
        neg_product_seq = self._unflatten_neg_seq(neg_product_seq, max_seq_len)
        neg_category_seq = self._unflatten_neg_seq(neg_category_seq, max_seq_len)
        
        # obtain embeddings
        neg_prd_emb = self.embedding_product(neg_product_seq)
        neg_cat_emb = self.embedding_category(neg_category_seq)

        # Step 2. Merge features

        if self.merge == 'elem_add':
            pos_emb = pos_prd_emb + pos_cat_emb
            neg_emb = neg_prd_emb + neg_cat_emb

        elif self.merge == 'concat_mlp':
            pos_emb = torch.tanh(self.mlp_merge(torch.cat((pos_prd_emb, pos_cat_emb), dim=-1)))
            neg_emb = torch.tanh(self.mlp_merge(torch.cat((neg_prd_emb, neg_cat_emb), dim=-1)))

        else:
            raise NotImplementedError

        pos_emb_inp = pos_emb[:, :-1]
        pos_emb_trg = pos_emb[:, 1:]
        neg_emb_inp = neg_emb[:, :, :-1]

        # Step3. Run forward pass on model architecture

        if self.is_rnn:
            # compute output through RNNs
            pos_emb_pred, _ = self.model(
                input=pos_emb_inp
            )
            model_outputs = (None, )
            
        else:
            # compute output through transformer
            model_outputs = self.model(
                inputs_embeds=pos_emb_inp,
            )
            pos_emb_pred = model_outputs[0]
            model_outputs = model_outputs[1:]

        trg_flat = product_seq_trg.flatten()
        non_pad_mask = (trg_flat != self.pad_token)        
        num_elem = non_pad_mask.sum()

        trg_flat_nonpad = torch.masked_select(trg_flat, non_pad_mask)

        # Step4. Compute logit and label for neg+pos samples

        # remove zero padding elements 
        pos_emb_pred = pos_emb_pred.flatten(end_dim=1)
        pos_emb_pred_fl = torch.masked_select(pos_emb_pred, non_pad_mask.unsqueeze(1).expand_as(pos_emb_pred))
        pos_emb_pred = pos_emb_pred_fl.view(-1, pos_emb_pred.size(1))

        pos_emb_trg = pos_emb_trg.flatten(end_dim=1)
        pos_emb_trg_fl = torch.masked_select(pos_emb_trg, non_pad_mask.unsqueeze(1).expand_as(pos_emb_trg))
        pos_emb_trg = pos_emb_trg_fl.view(-1, pos_emb_trg.size(1))

        # neg_emb_inp: (n_batch x n_negex x seqlen x emb_dim) -> (n_batch x seqlen x n_negex x emb_dim)
        neg_emb_inp = neg_emb_inp.permute(0, 2, 1, 3)
        neg_emb_inp_fl = neg_emb_inp.reshape(-1, neg_emb_inp.size(2), neg_emb_inp.size(3))
        neg_emb_inp_fl = torch.masked_select(neg_emb_inp_fl, non_pad_mask.unsqueeze(1).unsqueeze(2).expand_as(neg_emb_inp_fl))
        neg_emb_inp = neg_emb_inp_fl.view(-1, neg_emb_inp.size(2), neg_emb_inp.size(3))

        # concatenate 
        pos_emb_pred_expanded = pos_emb_pred.unsqueeze(1).expand_as(neg_emb_inp)
        pred_emb_flat = torch.cat((pos_emb_pred.unsqueeze(1), pos_emb_pred_expanded), dim=1).flatten(end_dim=1)
        trg_emb_flat = torch.cat((pos_emb_trg.unsqueeze(1), neg_emb_inp), dim=1).flatten(end_dim=1)

        n_pos_ex = pos_emb_trg.size(0)
        n_neg_ex = neg_emb_inp.size(0) * neg_emb_inp.size(1)
        labels = torch.LongTensor([0] * n_pos_ex).to(self.device)

        # compute similarity
        if self.similarity_type == 'concat_mlp':
            pos_cos_score = self.sim_mlp(torch.cat((pos_emb_pred, pos_emb_trg), dim=1))
            neg_cos_score = self.sim_mlp(torch.cat((pos_emb_pred_expanded, neg_emb_inp), dim=2)).squeeze(2)
        elif self.similarity_type == 'cosine':
            pos_cos_score = torch.cosine_similarity(pos_emb_pred, pos_emb_trg).unsqueeze(1)
            neg_cos_score = torch.cosine_similarity(pos_emb_pred_expanded, neg_emb_inp, dim=2)

        # compute predictionss (logits)
        cos_sim_concat = torch.cat((pos_cos_score, neg_cos_score), dim=1)
        items_prob_log = F.log_softmax(cos_sim_concat, dim=1)
        predictions = torch.exp(items_prob_log)

        # Step5. Compute loss and accuracy

        if self.loss_type in ['cross_entropy_neg', 'cross_entropy_neg_1d']:

            loss = self.loss_fn(items_prob_log, labels)

        elif self.loss_type == 'margin_hinge':

            _label = torch.LongTensor([1] * n_pos_ex + [-1] * n_neg_ex).to(pred_emb_flat.device)

            loss = self.loss_fn(pred_emb_flat, trg_emb_flat, _label) / num_elem 

        elif self.loss_type == 'cross_entropy':

            # compute logits (predicted probability of item ids)
            logits_all = self.output_layer(pos_emb_pred)
            pred_flat = self.log_softmax(logits_all)

            loss = self.loss_fn(pred_flat, trg_flat_nonpad)
            
        else:
            raise NotImplementedError

        # accuracy
        _, max_idx = torch.max(cos_sim_concat, dim=1)
        train_acc = (max_idx == 0).sum(dtype=torch.float32) / num_elem

        outputs = (train_acc, loss, predictions, labels) + model_outputs  # Keep mems, hidden states, attentions if there are in it

        return outputs  # return (train_acc), (loss), (predictions), (labels), (mems), (hidden states), (attentions)


def nll_1d(items_prob, _label=None):
    # https://github.com/gabrielspmoreira/chameleon_recsys/blob/da7f73a2b31d6867d444eded084044304b437413/nar_module/nar/nar_model.py#L639
    items_prob = torch.exp(items_prob)
    positive_prob = items_prob[:, 0]
    xe_loss = torch.log(positive_prob)
    cosine_sim_loss = - torch.mean(xe_loss)
    return cosine_sim_loss