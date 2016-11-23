from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf

from ugrnn.molecule import Molecule

"""Builds the UGRNN network.

Implements the inference/loss/training pattern for model building.

1. inference() - Builds the model as far as is required for running the network
forward to make predictions.
2. loss() - Adds to the inference model the layers required to generate loss.
3. training() - Adds to the loss model the Ops required to generate and
apply gradients.

"""


class Network(object):
    def __init__(self, name, encoding_nn_hidden_size, encoding_nn_output_size,
                 output_nn_hidden_size, activation_type, loss_type, max_seq_len,
                 feature_pl, path_pl, sequence_len, target_pl, learning_rate):
        """Build the ugrnn model up to where it may be used for inference."""
        self.name = name
        self.encoding_nn_output_size = encoding_nn_output_size
        self.output_nn_hidden_size = output_nn_hidden_size
        self.feature_pl = feature_pl
        self.path_pl = path_pl
        self.target_pl = target_pl
        self.loss_type = loss_type
        self.sequence_len = sequence_len

        self.activation_fun = Network.get_activation_fun(activation_type)

        flattened_idx_offset = tf.range(0, sequence_len) * max_seq_len * 4
        encoding_nn_input_size = 4 * encoding_nn_output_size + Molecule.num_of_features()

        with tf.variable_scope("EncodingNN") as scope:
            step = tf.constant(0)
            contextual_features = tf.get_variable("contextual_features",
                                                  [max_seq_len * max_seq_len * 4, encoding_nn_output_size],
                                                  dtype=tf.float32,
                                                  initializer=tf.constant_initializer(0),
                                                  trainable=False)

            contextual_features = contextual_features.assign(
                tf.zeros([max_seq_len * max_seq_len * 4, encoding_nn_output_size],
                         dtype=tf.float32))

            with tf.variable_scope('hidden1') as scope:
                weights = Network.weight_variable([encoding_nn_input_size, encoding_nn_hidden_size])
                Network.variable_summaries(weights, scope.name + '/weights')

                biases = Network.bias_variable([encoding_nn_hidden_size])
                Network.variable_summaries(weights, scope.name + '/biases')

            with tf.variable_scope('output') as scope:
                weights = Network.weight_variable([encoding_nn_hidden_size, encoding_nn_output_size])
                Network.variable_summaries(weights, scope.name + '/weights')

                biases = Network.bias_variable([encoding_nn_output_size])
                Network.variable_summaries(weights, scope.name + '/biases')

            _, step, _, _, _, contextual_features, _, _ = tf.while_loop(Network.cond, Network.body,
                                                                        [sequence_len, step, feature_pl, path_pl,
                                                                         flattened_idx_offset, contextual_features,
                                                                         encoding_nn_output_size, activation_type],
                                                                        back_prop=True,
                                                                        swap_memory=False, name=None)

            # use flattened indices1
            step_contextual_features = Network.get_contextual_feature(contextual_features=contextual_features,
                                                                      index=0,
                                                                      flattened_idx_offset=flattened_idx_offset,
                                                                      encoding_nn_output_size=encoding_nn_output_size)

            input_begin = tf.get_variable("input_begin", [3], dtype=tf.int32)
            input_begin = tf.scatter_update(input_begin, 1, step, use_locking=None)
            step_feature = tf.squeeze(tf.slice(feature_pl, input_begin, [-1, 1, -1]), squeeze_dims=[1])

            inputs = tf.concat(1, [step_contextual_features, step_feature])
            encodings = Network.apply_EncodingNN(inputs, activation_type)

            molecule_encoding = tf.expand_dims(tf.reduce_sum(encodings, 0), 0)

        with tf.variable_scope("OutputNN") as scope:
            hidden1 = Network.nn_layer(input_tensor=molecule_encoding,
                                       input_dim=encoding_nn_output_size,
                                       output_dim=output_nn_hidden_size,
                                       layer_name='hidden1',
                                       act=self.activation_fun)

            self.prediction_op = Network.nn_layer(input_tensor=hidden1,
                                                  input_dim=output_nn_hidden_size,
                                                  output_dim=1,
                                                  layer_name='output',
                                                  act=tf.identity)

        self.add_loss_ops()
        self.add_training_ops(learning_rate)

    @staticmethod
    def get_activation_fun(activation_fun):
        if activation_fun is 'tanh':
            return tf.nn.tanh
        else:
            return tf.nn.relu

    @staticmethod
    def cond(sequence_len,
             step,
             feature_pl,
             path_pl,
             flattened_idx_offset,
             contextual_features,
             encoding_nn_output_size,
             activation_type):
        return tf.less(step, sequence_len - 1)

    @staticmethod
    def body(sequence_len,
             step,
             feature_pl,
             path_pl,
             flattened_idx_offset,
             contextual_features,
             encoding_nn_output_size,
             activation_type):

        input_begin = tf.get_variable("input_begin", [3], dtype=tf.int32, initializer=tf.constant_initializer(0),
                                      trainable=False)
        input_begin = tf.scatter_update(input_begin, 1, step, use_locking=None)

        step_feature = tf.squeeze(tf.slice(feature_pl, input_begin, [-1, 1, -1]))

        input_idx = tf.slice(path_pl, input_begin, [-1, 1, 1])
        input_idx = tf.reshape(input_idx, [-1])

        output_begin = tf.get_variable("ouput_begin", [3], dtype=tf.int32, initializer=tf.constant_initializer(0),
                                       trainable=False)
        output_begin = tf.scatter_update(output_begin, 1, step, use_locking=None)
        output_begin = tf.scatter_update(output_begin, 2, 1, use_locking=None)

        tf.get_variable_scope().reuse_variables()

        contextual_features = tf.get_variable("contextual_features")

        step_contextual_features = Network.get_contextual_feature(contextual_features=contextual_features,
                                                                  index=input_idx,
                                                                  flattened_idx_offset=flattened_idx_offset,
                                                                  encoding_nn_output_size=encoding_nn_output_size)

        nn_inputs = tf.concat(1, [step_contextual_features, step_feature])
        updated_contextual_vectors = Network.apply_EncodingNN(nn_inputs, activation_type)
        output_idx = tf.squeeze(tf.slice(path_pl, output_begin, [-1, 1, 2]))

        contextual_features = Network.update_contextual_features(contextual_features=contextual_features,
                                                                 indices=output_idx,
                                                                 updates=updated_contextual_vectors,
                                                                 flattened_idx_offset=flattened_idx_offset)

        with tf.control_dependencies([contextual_features]):
            return (sequence_len,
                    step + 1,
                    feature_pl,
                    path_pl,
                    flattened_idx_offset,
                    contextual_features,
                    encoding_nn_output_size,
                    activation_type)

    def add_training_ops(self, learning_rate):

        def clip_gradient(gradient):
            if gradient is not None:
                return tf.mul(tf.clip_by_value(tf.abs(grad), 0.1, 1.), tf.sign(grad))
            else:
                return None

        # optimizer = tf.train.GradientDescentOptimizer(learning_rate=learning_rate)
        optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate, beta1=0.9, beta2=0.999, epsilon=1e-08,
                                           use_locking=False, name='Adam')
        gvs = optimizer.compute_gradients(self.loss_op)
        capped_gvs = [(clip_gradient(grad), var) for grad, var in gvs]
        self.train_op = optimizer.apply_gradients(capped_gvs)

    def add_loss_ops(self):

        def rmse_loss(predictions, targets):
            return tf.nn.l2_loss(predictions - targets, name='l2_loss')

        def aae_loss(predictions, targets):
            return tf.reduce_sum(tf.abs(predictions - targets), name='l1_loss')

        if self.loss_type is 'aae':
            self.loss_op = aae_loss(self.prediction_op, self.target_pl)
        else:
            self.loss_op = rmse_loss(self.prediction_op, self.target_pl)

        tf.scalar_summary(self.loss_op.op.name, self.loss_op)

    """
    Contextual vector is flatted array
    index is 1D index with
    """

    @staticmethod
    def get_contextual_feature(contextual_features, index, flattened_idx_offset, encoding_nn_output_size):
        indices = index + flattened_idx_offset
        values = [indices, indices, indices, indices]
        indices = tf.pack(values, axis=1, name='pack')
        indices = indices + tf.constant([0, 1, 2, 3])
        indices = tf.reshape(indices, [-1])
        contextual_vector = tf.gather(contextual_features, indices)
        contextual_vector = tf.reshape(contextual_vector, [-1, 4 * encoding_nn_output_size])
        return contextual_vector

    @staticmethod
    def update_contextual_features(contextual_features, indices, updates, flattened_idx_offset):
        first_indices, second_indices = tf.split(1, 2, indices)
        indices = tf.squeeze(first_indices + second_indices)
        indices = indices + flattened_idx_offset
        contextual_features = tf.scatter_add(contextual_features, indices, updates, use_locking=None)
        return contextual_features

    @staticmethod
    def nn_layer(input_tensor, input_dim, output_dim, layer_name, act):
        """Reusable code for making a simple neural net layer.
        It does a matrix multiply, bias add, and then uses relu to nonlinearize.
        It also sets up name scoping so that the resultant graph is easy to read,
        and adds a number of summary ops.
        """
        # Adding a name scope ensures logical grouping of the layers in the graph.
        with tf.variable_scope(layer_name) as scope:
            weights = Network.weight_variable([input_dim, output_dim])
            Network.variable_summaries(weights, layer_name + '/weights')

            biases = Network.bias_variable([output_dim])
            Network.variable_summaries(biases, layer_name + '/biases')

            preactivate = tf.matmul(input_tensor, weights) + biases
            tf.histogram_summary(layer_name + '/pre_activations', preactivate)

            activations = act(preactivate, name='activation')
            tf.histogram_summary(layer_name + '/activations', activations)

            return activations

    @staticmethod
    def weight_variable(shape):
        return tf.get_variable(name="weights",
                               shape=shape,
                               initializer=tf.contrib.layers.xavier_initializer(),
                               trainable=True,
                               collections=['WEIGHTS', tf.GraphKeys.VARIABLES])

    @staticmethod
    def bias_variable(shape):
        """Create a bias variable with appropriate initialization."""
        return tf.get_variable(name="biases",
                               shape=shape,
                               initializer=tf.constant_initializer(0.1),
                               trainable=True)

    @staticmethod
    def apply_EncodingNN(inputs, activation_type):
        activation_fun = Network.get_activation_fun(activation_type)
        with tf.variable_scope('hidden1') as scope:
            weights = tf.get_variable("weights")
            biases = tf.get_variable("biases")

            hidden1 = activation_fun(tf.matmul(inputs, weights) + biases)

        with tf.variable_scope('output') as scope:
            weights = tf.get_variable("weights")
            biases = tf.get_variable("biases")
            return activation_fun(tf.matmul(hidden1, weights) + biases)

    @staticmethod
    def variable_summaries(var, name):
        """Attach a lot of summaries to a Tensor."""
        with tf.name_scope('summaries'):
            mean = tf.reduce_mean(var)
            tf.scalar_summary('mean/' + name, mean)
            with tf.name_scope('stddev'):
                stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
            tf.scalar_summary('stddev/' + name, stddev)
            tf.scalar_summary('max/' + name, tf.reduce_max(var))
            tf.scalar_summary('min/' + name, tf.reduce_min(var))
            tf.histogram_summary(name, var)
