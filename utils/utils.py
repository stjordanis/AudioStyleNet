"""
File for general usefull functions which are not specific to a certain module
"""

import cv2
import dlib
import matplotlib.pyplot as plt
import numpy as np
import os
import time
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms as transforms


class Config(dict):
    def __init__(self, *args, **kwargs):
        super(Config, self).__init__(*args, **kwargs)
        self.__dict__ = self


class Denormalize(object):
    """ Custom transform: Denormalizes image to save or display it """
    def __init__(self, mean, std):
        assert len(mean) == 3
        assert len(std) == 3
        self.transform = transforms.Compose([
            transforms.Normalize([0., 0., 0.], 1 / np.array(std)),
            transforms.Normalize(-np.array(mean), [1., 1., 1.])]
        )

    def __call__(self, sample):
        return self.transform(sample)


class Downsample(object):
    """ Custom transform: Downsamples image in StyleGAN2 manner """
    def __init__(self, size):
        self.size = size

    def __call__(self, sample):
        c, h, w = sample.shape
        if h > self.size:
            factor = h // self.size
            sample = sample.reshape(
                c, h // factor, factor, w // factor, factor)
            sample = sample.mean([2, 4])
        return sample


def downsample_256(img):
    b, c, h, w = img.shape
    factor = h // 256
    img = img.reshape(b, c, h // factor, factor, w // factor, factor)
    img = img.mean([3, 5])
    return img


class GradPlotter:
    """
    Source: https://discuss.pytorch.org/t/check-gradient-flow-in-network/15063/10
    Plots gradients flowing through different layers in the net during training.
    Can be used for checking possible gradient vanishing / exploding problems.

    Usage: Plug this function in Trainer class after loss.backwards() as
    'if grad_plotter is None:
        grad_plotter = GradPlotter(self.model.named_parameters())
    grad_plotter.plot_grad_flow(self.model.named_parameters())'
    to visualize the gradient flow
    """
    def __init__(self, named_parameters):
        ave_grads = []
        max_grads = []
        layers = []
        for n, p in named_parameters:
            if p.requires_grad and ("bias" not in n):
                layers.append(n)
                # if n != 'fc.weight':
                #     print(n, torch.sum(p.grad.abs()))
                ave_grads.append(p.grad.abs().mean())
                max_grads.append(p.grad.abs().max())

        plt.ion()
        fig = plt.figure()
        ax = fig.add_subplot(111)

        bar1 = ax.bar(np.arange(len(max_grads)), max_grads, alpha=0.1, lw=1, color="c")
        bar2 = ax.bar(np.arange(len(max_grads)), ave_grads, alpha=0.1, lw=1, color="b")

        ax.hlines(0, 0, len(ave_grads) + 1, lw=2, color="k")
        ax.set_xticks(range(0, len(ave_grads), 1), layers)
        ax.set_xlim(left=0, right=len(ave_grads))
        ax.set_ylim(bottom=-0.001,
                    top=max([tensor.cpu() for tensor in ave_grads]))  # zoom in on the lower gradient regions
        ax.set_xlabel("Layers")
        ax.set_ylabel("average gradient")
        ax.set_title("Gradient flow")
        ax.grid(True)
        ax.legend([plt.Line2D([0], [0], color="c", lw=4),
                   plt.Line2D([0], [0], color="b", lw=4),
                   plt.Line2D([0], [0], color="k", lw=4)],
                  ['max-gradient', 'mean-gradient', 'zero-gradient'])

        self.fig = fig
        self.ax = ax
        self.bar1 = bar1
        self.bar2 = bar2

    def plot_grad_flow(self, named_parameters):
        ave_grads = []
        max_grads = []
        layers = []
        for n, p in named_parameters:
            if p.requires_grad and ("bias" not in n):
                layers.append(n)
                ave_grads.append(p.grad.abs().mean())
                max_grads.append(p.grad.abs().max())

        for rect, h in zip(self.bar1, max_grads):
            rect.set_height(h)
        for rect, h in zip(self.bar2, ave_grads):
            rect.set_height(h)
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()


def time_to_str(t):
    return "{:.0f}h {:.0f}m {:.0f}s".format(t // 3600, (t // 60) % 60, t % 60)


def time_left(t_start, n_iters, i_iter):
    iters_left = n_iters - i_iter
    time_per_iter = (time.time() - t_start) / i_iter
    time_remaining = time_per_iter * iters_left
    return time_to_str(time_remaining)


def count_params(model):
    return sum(p.numel() for p in model.parameters())


def count_trainable_params(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def fig2img(fig):
    """
    Convert a matplotlib figure into a PIL Image

    Arguments:
        fig (matplotlib.figure.Figure): Input figure

    Returns:
        img (PIL.Image.Image): Output image
    """
    fig.canvas.draw()
    w, h = fig.canvas.get_width_height()
    buf = np.frombuffer(fig.canvas.tostring_argb(), dtype=np.uint8).reshape(w, h, 4)
    buf = np.roll(buf, 3, axis=2).transpose((2, 0, 1))
    img = torch.tensor(buf / 255.)
    return img


class GANLoss(nn.Module):
    # Source: https://github.com/NVlabs/SPADE/blob/master/models/networks/loss.py
    """
    Define different GAN objectives.
    The GANLoss class abstracts away the need to create the target label tensor
    that has the same size as the input.
    """

    def __init__(self, gan_mode, device, target_real_label=1.0,
                 target_fake_label=0.0, flip_p=0., noisy_labels=False,
                 label_range_real=(1.0, 1.0), label_range_fake=(0.0, 0.0)):
        """
        Initialize the GANLoss class.

        Parameters:
            gan_mode (str): the type of GAN objective. It currently supports vanilla, lsgan, and wgan.
            target_real_label (bool): label for a real image
            target_fake_label (bool): label of a fake image
            flip_p (float): probability of flipping labels in discriminator
            noisy_labels (bool): Use noisy labels or not
            label_range_real (tuple of floats): Min and max for real labels if noisy_labels == True
            label_range_fake (tuple of floats): Min and max for fake labels if noisy_labels == True

        Note: Do not use sigmoid as the last layer of Discriminator.
        LSGAN needs no sigmoid. vanilla GANs will handle it with BCEWithLogitsLoss.
        """
        super(GANLoss, self).__init__()
        self.device = device

        # Training labels
        self.register_buffer('real_label', torch.tensor(target_real_label))
        self.register_buffer('fake_label', torch.tensor(target_fake_label))

        # Flip labels
        self.flip_prob = torch.distributions.bernoulli.Bernoulli(flip_p)

        # Noisy labels
        self.noisy_labels = noisy_labels
        self.label_range_real = label_range_real
        self.label_range_fake = label_range_fake

        # Gan mode
        self.gan_mode = gan_mode
        if gan_mode == 'lsgan':
            pass
        elif gan_mode == 'vanilla':
            pass
        elif gan_mode == 'wgan':
            pass
        else:
            raise NotImplementedError('gan mode %s not implemented' % gan_mode)

    def get_target_tensor(self, prediction, target_is_real, for_discriminator):
        """Create label tensors with the same size as the input.
        Parameters:
            prediction (tensor): tpyically the prediction from a discriminator
            target_is_real (bool): if the ground truth label is for real images or fake images
        Returns:
            A label tensor filled with ground truth label, and with the size of the input
        """

        if target_is_real:
            target_tensor = self.real_label.expand_as(prediction)
            if for_discriminator:
                if self.noisy_labels:
                    mini, maxi = self.label_range_real
                    noise = torch.rand(prediction.size()) * (maxi - mini) - (1. - abs(mini))
                    target_tensor = target_tensor + noise
                target_tensor = self.flip_labels(target_tensor)
        else:
            target_tensor = self.fake_label.expand_as(prediction)
            if for_discriminator:
                if self.noisy_labels:
                    mini, maxi = self.label_range_fake
                    noise = torch.rand(prediction.size()) * (maxi - mini) - (0. - abs(mini))
                    target_tensor = target_tensor + noise
                target_tensor = self.flip_labels(target_tensor)

        return target_tensor.to(self.device)

    def flip_labels(self, target_tensor):
        """
        Randomly flip labels of target_tensor with a probability provided by
        flip_p during init.

        Parameters:
            target_tensor (torch.tensor): tensor with labels
        """
        flip_idx = self.flip_prob.sample((target_tensor.size())).bool()
        target_tensor[flip_idx] = 1 - target_tensor[flip_idx]
        return target_tensor

    def loss(self, prediction, target_is_real, for_discriminator=True):
        if self.gan_mode == 'vanilla':  # cross entropy loss
            target_tensor = self.get_target_tensor(prediction, target_is_real, for_discriminator)
            loss = F.binary_cross_entropy_with_logits(prediction, target_tensor)
            return loss
        elif self.gan_mode == 'lsgan':
            target_tensor = self.get_target_tensor(prediction, target_is_real, for_discriminator)
            return F.mse_loss(prediction, target_tensor)
        else:
            # wgan
            if target_is_real:
                return -prediction.mean()
            else:
                return prediction.mean()

    def __call__(self, prediction, target_is_real, for_discriminator=True):
        """
        Calculate loss given Discriminator's output and grount truth labels.

        Parameters:
            prediction (tensor): tpyically the prediction output from a discriminator
            target_is_real (bool): if the ground truth label is for real images or fake images
            for_discriminator (bool): Indicates discriminator. Noisy labels are only for D
        Returns:
            the calculated loss.
        """
        # computing loss is a bit complicated because |input| may not be
        # a tensor, but list of tensors in case of multiscale discriminator
        if isinstance(prediction, list):
            loss = 0
            for pred_i in prediction:
                if isinstance(pred_i, list):
                    pred_i = pred_i[-1]
                loss_tensor = self.loss(pred_i, target_is_real, for_discriminator)
                bs = 1 if len(loss_tensor.size()) == 0 else loss_tensor.size(0)
                new_loss = torch.mean(loss_tensor.view(bs, -1), dim=1)
                loss += new_loss
            return loss / len(prediction)
        else:
            return self.loss(prediction, target_is_real, for_discriminator)


class FaceMaskPredictor:
    def __init__(self):
        super().__init__()
        self.detector = dlib.get_frontal_face_detector()
        self.predictor = dlib.shape_predictor(
            os.path.expanduser('~') + '/Datasets/RAVDESS/shape_predictor_68_face_landmarks.dat')

    def _get_landmarks(self, img):
        dets = self.detector(img, 1)
        for detection in dets:
            landmarks = [(item.x, item.y)
                         for item in self.predictor(img, detection).parts()]
            break
        return landmarks

    def _compute_face_mask(self, landmarks, image):
        jaw = landmarks[0:17]
        left_eyebrow = landmarks[17:20]
        left_eyebrow[:, 1] = left_eyebrow[:, 1] - 10
        right_eyebrow = landmarks[24:27]
        right_eyebrow[:, 1] = right_eyebrow[:, 1] - 10
        hull = np.concatenate(
            (jaw, np.flip(right_eyebrow, 0), np.flip(left_eyebrow, 0)))
        mask = np.zeros(image.shape, dtype='uint8')
        mask = cv2.drawContours(mask, [hull], -1,
                                (255, 255, 255), thickness=cv2.FILLED)
        mask = cv2.bitwise_not(mask)
        img2gray = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(img2gray, 10, 255, cv2.THRESH_BINARY_INV)
        return mask

    def get_mask(self, img):
        """
        args:
            img: torch.tensor, shape: [1, c, h, w]
        """
        img = img.detach().cpu().numpy()[0]
        img = (img * 255).astype('uint8').transpose(1, 2, 0)

        landmarks = np.array(self._get_landmarks(img))
        mask = self._compute_face_mask(landmarks, img)
        mask = torch.tensor(mask[None, None, :] / 255., dtype=torch.float32)
        return mask


def get_mouth_params(landmarks, frame):
    # Select mouth landmarks only
    mouth_lm = landmarks[48:68]

    # Distance parameters
    d_params = distance_params(mouth_lm)

    # Angle parameters
    a_params = angle_params(mouth_lm, frame)

    # Surface parameters
    s_params = surface_params(mouth_lm)

    # Textural parameters
    t_params = texture_params(mouth_lm, frame)

    params = np.concatenate((
        d_params,
        a_params,
        s_params,
        t_params
    )).astype(np.float32)

    return params


def distance_params(mouth_lm):

    def euc_dist(a, b):
        return np.linalg.norm(a - b)

    """
    1. parameters representing the distance between the successive points on
    the outer periphery of the contour delineated on the speaker’s lips
    relative to their sum, i.e. the circumference. 12 parameters were
    calculated for the outer contour.
    """
    def distance_params_1_and_2(mouth_lm):
        lm = np.concatenate((mouth_lm, mouth_lm[None, 0]), axis=0)
        dists = np.array([euc_dist(a, b) for a, b in zip(lm[:-1], lm[1:])])
        circumference = dists.sum()
        normed_dists = dists / circumference
        return normed_dists

    dists1 = distance_params_1_and_2(mouth_lm[:12])

    """
    2. the same parameters calculated for the inner contour. 8 parameters were
    calculated for the internal contour.
    """
    dists2 = distance_params_1_and_2(mouth_lm[12:20])

    """
    3. the distances of the straight lines connecting vertically the outer and
    inner contour points on the mouth in relation to the longest straight line
    in the horizontal plane. They depict the maximum opening of the mouth in
    successive sections along the mouth, from left to right. The maximum
    opening found – 1 parameter. The opening for outer lips – 5 parameters.
    For inner lips – 3 parameters.
    """
    def distance_params_3(mouth_lm):
        longest_horizontal = mouth_lm[:, 0].max() - mouth_lm[:, 0].min()
        # Outer
        outer = np.array([euc_dist(a, b) for a, b in zip(
            mouth_lm[1:6], np.flip(mouth_lm[7:12], axis=0))]) / longest_horizontal
        # Inner
        inner = np.array([euc_dist(a, b) for a, b in zip(
            mouth_lm[13:16], np.flip(mouth_lm[17:20], axis=0))]) / longest_horizontal
        return np.concatenate((outer.max().reshape(-1,), outer, inner))

    dists3 = distance_params_3(mouth_lm)

    """
    4. the distances representing height versus maximum width, calculated for
    the exposure of the upper and lower lip while uttering a given viseme.
    They show the degree of lip exposure. 5 parameters were determined for the
    upper lip and 5 for the lower lip.
    """
    def distance_params_4(mouth_lm):
        longest_horizontal = mouth_lm[:, 0].max() - mouth_lm[:, 0].min()
        # Upper
        upper = np.array([euc_dist(a, b) for a, b in zip(
            mouth_lm[1:6], mouth_lm[12:17])]) / longest_horizontal
        # Lower
        lower = np.array([euc_dist(a, b) for a, b in zip(mouth_lm[7:12], np.concatenate(
            (mouth_lm[16:20], mouth_lm[None, 12])))]) / longest_horizontal
        return np.concatenate((upper, lower))

    dists4 = distance_params_4(mouth_lm)

    return np.concatenate((dists1, dists2, dists3, dists4))


def angle_params(mouth_lm, frame):

    def angle(a, b, c):
        ba = a - b
        bc = c - b
        numerator = np.dot(ba, bc)
        denominator = np.linalg.norm(ba) * np.linalg.norm(bc)
        if denominator == 0:
            print("Warning, two points in angle are equal")
            return np.pi
        cosine_angle = numerator / denominator
        cosine_angle = min(1., max(-1., cosine_angle))
        if cosine_angle < -1 or cosine_angle > 1:
            print(a, b, c)
            print(ba, bc)
            print(cosine_angle)
            1 / 0
        return np.arccos(cosine_angle)

    def angle_params_1_and_2(lm):
        angles = np.array([angle(a, b, c)
                           for a, b, c in zip(lm[:-2], lm[1:-1], lm[2:])])
        return angles

    """
    1. 12 parameters calculated for the outer contour of the lips representing
    the values of the angles between successive points delineated on the lips
    in degrees. Two straight lines were defined, drawn through successive
    points, which helped to calculate the angle values.
    """
    angle1 = angle_params_1_and_2(
        np.concatenate((mouth_lm[:12], mouth_lm[:2])))

    """
    2. 8 parameter values defined in a similar manner for the angles of the
    inner contour.
    """
    angle2 = angle_params_1_and_2(
        np.concatenate((mouth_lm[12:20], mouth_lm[12:14])))

    return np.concatenate((angle1, angle2))


def surface_params(mouth_lm):
    def area(x, y):
        return 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))

    upper_outer = mouth_lm[:7]
    upper_inner = mouth_lm[12:17]
    lower_outer = np.concatenate((mouth_lm[6:12], mouth_lm[None, 0]))
    lower_inner = np.concatenate((mouth_lm[16:20], mouth_lm[None, 12]))

    upper_nodes = np.concatenate((upper_outer, np.flip(upper_inner, axis=0)))
    lower_nodes = np.concatenate((lower_outer, np.flip(lower_inner, axis=0)))
    inner_nodes = mouth_lm[12:20]
    outer_nodes = mouth_lm[:12]

    upper_area = area(upper_nodes[:, 0], upper_nodes[:, 1])
    lower_area = area(lower_nodes[:, 0], lower_nodes[:, 1])

    inner_area = area(inner_nodes[:, 0], inner_nodes[:, 1])
    outer_area = area(outer_nodes[:, 0], outer_nodes[:, 1])

    """
    1. the first parameter is the ratio of the area limited by the inner
    contour of the lips to the total area of the mouth, calculated for the
    outer contour.
    """
    surface1 = inner_area / outer_area

    """
    2. another element of the parameter vector is the ratio of the upper lip
    and lower lip area to the total area of the mouth.
    """
    surface2 = (upper_area + lower_area) / outer_area

    """
    3. the next value defined is the ratio of the area limited by the inner
    lip contour to the surface of the upper lip.
    """
    surface3 = inner_area / upper_area

    """
    4. similarly to the previous parameter, the following one is the ratio of
    the inner area to the lower lip area.
    """
    surface4 = inner_area / lower_area

    """
    5. another parameter is the ratio of the upper lip area to the lower lip.
    """
    surface5 = upper_area / lower_area

    """
    6. the next parameter is the ratio of the surface of the inner contour of
    the lips to the total surface of the lips.
    """
    surface6 = inner_area / (outer_area - inner_area)

    """
    7. the last two parameters are the total area of the upper lip and the
    area inside the mouth to the surface of the lower lip and the sum of the
    lower lip and the area inside the mouth to the surface of the upper lip.
    """
    surface7 = (upper_area + inner_area) / lower_area
    surface8 = (lower_area + inner_area) / upper_area

    return np.concatenate((
        surface1.reshape(-1,),
        surface2.reshape(-1,),
        surface3.reshape(-1,),
        surface4.reshape(-1,),
        surface5.reshape(-1,),
        surface6.reshape(-1,),
        surface7.reshape(-1,),
        surface8.reshape(-1,)
    ))


def texture_params(mouth_lm, frame):
    roi = frame[mouth_lm[:, 1].min():mouth_lm[:, 1].max(),
                mouth_lm[:, 0].min():mouth_lm[:, 0].max()]

    """
    1. 32 parameters representing the mouth histogram in shades of grayscale.
    """
    roi_gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    hist_gray, _ = np.histogram(roi_gray.flatten(), 32, [0, 256])

    """
    2. 32 parameters that represent the mouth histogram within the HSV colour
    scale.
    """
    roi_hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
    hist_hsv, _ = np.histogram(roi_hsv.flatten(), 32, [0, 256])

    """
    3. 32 parameters for the mouth image histogram in grayscale after applying
    the equalization.
    """
    roi_gray_equ = cv2.equalizeHist(roi_gray)
    hist_gray_equ, _ = np.histogram(roi_gray_equ, 32, [0, 256])

    """
    4. 32 parameters for the mouth image histogram in grayscale after
    processing via the Contrast Adaptive Histogram Equalization (CLAHE).
    """
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    roi_gray_clahe = clahe.apply(roi_gray)
    hist_gray_clahe, _ = np.histogram(roi_gray_clahe, 32, [0, 256])

    """
    5. 32 parameters that represent the most significant values of DCT for the
    mouth area read in accordance with the Zig-Zag curve.
    """
    # Convert to float
    roi_gray_f = np.float32(roi_gray)
    # Pad if necessary
    height, width = roi_gray_f.shape[0:2]
    bottom = (height % 2 == 1)
    right = (width % 2 == 1)
    roi_gray_f = cv2.copyMakeBorder(
        roi_gray_f, 0, bottom, 0, right, cv2.BORDER_REFLECT101)
    # Compute dct
    dct = cv2.dct(roi_gray_f)
    roi_dct = np.uint8(dct)
    # Read 32 params in zig-zag manner
    height, width = roi_dct.shape[0:2]
    r, c = 0, 0
    dct_params = []
    for i in range(min(32, max(height, width))):
        dct_params.append(dct[c, r])
        if c < height - 1:
            c += 1
        if r < width - 1:
            r += 1
    dct_params = np.array(dct_params)

    texture = np.concatenate((
        hist_gray,
        hist_hsv,
        hist_gray_equ,
        hist_gray_clahe,
        dct_params
    ))

    return texture
